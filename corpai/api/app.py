"""create_app(): the FastAPI app with the API, the OpenAI-compatible gateway endpoint, the MCP
servers and the UI (architecture §13).

Graphs call the MCP servers in-process: Services.mcp is McpClient(asgi_app=<this app>), so a tool
call goes through the same HTTP path (/mcp/<domain>/mcp, Bearer tool token, MCP protocol) without a
network port. A deployment that splits the servers out only changes McpClient's base_url.

Lifespan: start/attach Postgres, apply migrations, install the app-role pool, load the person-name
gazetteer (if that fails the gateway keeps failing closed: everything routes local; the runner
re-checks it before every run, corpai/agents/_gazetteer.py), mark proposals a crash left
'approved' as failed, set up the LangGraph checkpoint tables (row-level security per thread owner)
as the owner, run the MCP session managers; close all on shutdown.

The UI is served without ui/fixtures/: those demo recordings are for the file: demo only and may
hold confidential sample answers, so the live server never hands them out (404, like any unknown
path), and index.html loads them only when opened from disk.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import anyio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row

from corpai.api import errors, routes_core, routes_ops, routes_proposals, routes_runs
from corpai.api.security import SecurityHeaders
from corpai.config import ROOT

log = logging.getLogger(__name__)

UI_DIR = ROOT / "ui"
_PLACEHOLDER = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Kestrelbyte AI</title>"
                "</head><body><h1>Kestrelbyte internal AI platform</h1><p>The UI is not installed (ui/ is "
                "missing). The API is at <code>/api/health</code>.</p></body></html>")


def _prepare_db() -> None:
    from corpai.db import pg
    from corpai.db.migrate import apply_migrations

    pg.ensure_server()
    with pg.connect_owner() as conn:
        apply_migrations(conn)


def _fail_interrupted() -> None:
    from corpai.proposals import fail_interrupted

    try:
        ids = fail_interrupted()
    except Exception as e:  # noqa: BLE001 - start-up must not die on this; approve() also recovers
        log.warning("checking for interrupted proposals failed: %s", type(e).__name__)
        return
    if ids:
        log.warning("%d approved proposal(s) were interrupted before execution and marked failed", len(ids))


def _lifespan(mcp_running: Any):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        from psycopg_pool import AsyncConnectionPool

        from corpai.agents import _checkpoint, _gazetteer, router
        from corpai.agents.runtime import Services
        from corpai.db import pg
        from corpai.gateway import get_gateway
        from corpai.mcp_client import McpClient
        from corpai.trace import tracer

        await anyio.to_thread.run_sync(_prepare_db)
        pool = AsyncConnectionPool(pg.app_dsn(), min_size=1, max_size=10, open=False,
                                   kwargs={"row_factory": dict_row})
        await pool.open()
        previous = pg.app_pool()
        pg.install_app_pool(pool)
        try:
            # fail closed: with no gazetteer the gateway raises every request to at least L3 (local)
            await _gazetteer.ensure_fresh(force=True)
            await anyio.to_thread.run_sync(_fail_interrupted)
            await _checkpoint.setup_schema()
            async with _checkpoint.open_checkpointer() as saver:
                gateway = getattr(app.state, "gateway", None) or get_gateway()
                app.state.services = Services(gateway=gateway, mcp=McpClient(asgi_app=app), tracer=tracer)
                app.state.checkpointer = saver
                try:
                    async with mcp_running:
                        yield
                finally:
                    router.forget_compiled(saver)
                    app.state.services = None
                    app.state.checkpointer = None
        finally:
            pg.install_app_pool(previous)
            await pool.close()

    return lifespan


class _LiveUI:
    """ui/ as static files, minus ui/fixtures/ (see module doc). Static data is never cached."""

    def __init__(self, directory: str):
        import os

        from fastapi.staticfiles import StaticFiles

        self.files = StaticFiles(directory=directory, html=True)
        self.root = os.path.realpath(directory)
        self.fixtures = os.path.normcase(os.path.join(self.root, "fixtures"))

    def _hidden(self, path: str) -> bool:
        import os

        rel = path.replace(chr(92), "/")
        # by name (Windows ignores case and trailing dots/spaces) and by where the path resolves to
        if any(seg.lower().rstrip(". ") == "fixtures" for seg in rel.split("/")):
            return True
        full = os.path.normcase(os.path.realpath(os.path.join(self.root, rel.lstrip("/"))))
        return full == self.fixtures or full.startswith(self.fixtures + os.sep)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.files(scope, receive, send)
            return
        if self._hidden(scope.get("path", "")):
            from starlette.responses import PlainTextResponse

            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return

        async def no_store(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = dict(message, headers=headers)
            await send(message)

        await self.files(scope, receive, no_store)


def _mount_ui(app: FastAPI) -> None:
    if (UI_DIR / "index.html").is_file():
        app.mount("/", _LiveUI(str(UI_DIR)), name="ui")
        return

    @app.get("/", include_in_schema=False)
    async def placeholder() -> HTMLResponse:
        return HTMLResponse(_PLACEHOLDER)


_OWN_ROUTES = frozenset({"routes_core", "routes_runs", "routes_proposals", "routes_ops"})


def _milestone_routers() -> list[Any]:
    """`router` of every other corpai/api/routes_<area>.py, so milestones add routes in their own file."""
    import importlib
    import pkgutil

    import corpai.api

    out = []
    for m in sorted(pkgutil.iter_modules(corpai.api.__path__), key=lambda m: m.name):
        if m.name.startswith("routes_") and m.name not in _OWN_ROUTES:
            mod = importlib.import_module(f"corpai.api.{m.name}")
            if getattr(mod, "router", None) is not None:
                out.append(mod.router)
    return out


def create_app(*, gateway: Any = None) -> FastAPI:
    """A new app. `gateway` (tests) replaces get_gateway() for graphs and /v1/chat/completions."""
    from corpai.gateway import openai_compat
    from corpai.mcp_servers import mount_all

    app = FastAPI(title="Kestrelbyte AI platform", docs_url=None, redoc_url=None, openapi_url=None)
    if gateway is not None:
        app.state.gateway = gateway
    app.state.services = None
    app.state.checkpointer = None
    errors.install_handlers(app)
    for r in (routes_core.router, routes_runs.router, routes_proposals.router, routes_ops.router,
              openai_compat.router, *_milestone_routers()):
        app.include_router(r)
    mcp_running = mount_all(app)  # /mcp/<domain>/mcp; before the UI mount, which catches everything else
    _mount_ui(app)
    app.router.lifespan_context = _lifespan(mcp_running)
    app.add_middleware(SecurityHeaders)
    return app
