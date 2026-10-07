"""MCP servers, one per domain (architecture §11, design-review 二.4).

Public API:
    DOMAINS                         ("crm", "code", "docs", "finance", "hris", "legal", "web")
    build_server(domain) -> MCPServer
    mount_all(app, domains=DOMAINS) -> async context manager
        mounts each server at /mcp/<domain> (endpoint /mcp/<domain>/mcp; stateless streamable HTTP,
        JSON responses) and returns the context manager that runs every session manager; the API
        lifespan enters it: `async with mount_all(app): yield`
    authenticate(ctx, domain, tool) -> AuthContext       (raises ToolDenied, audited)
    ToolDenied, AuthContext
    run_standalone(domain, host, port)                    one server on its own (python -m corpai.mcp_servers)

Every tool: authenticate → policy.decide() per row → SQL in principal_tx (RLS) → envelope with a
Label → audit tool_call. Write tools only file proposals (corpai.proposals).
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator, Iterable

from mcp.server import MCPServer

from corpai.mcp_servers._auth import AuthContext, ToolDenied, authenticate

DOMAINS: tuple[str, ...] = ("crm", "code", "docs", "finance", "hris", "legal", "web")
MOUNT_PREFIX = "/mcp"
ENDPOINT_PATH = "/mcp"


def _tools(domain: str) -> list:
    from corpai.mcp_servers import code, crm, docs, finance, hris, legal, web

    modules = {"crm": crm, "code": code, "docs": docs, "finance": finance, "hris": hris, "legal": legal, "web": web}
    if domain not in modules:
        raise ValueError(f"unknown MCP domain {domain!r}")
    return modules[domain].TOOLS


def build_server(domain: str) -> MCPServer:
    """A fresh MCPServer for `domain` with every catalogue tool registered (tool names = function
    names; a test checks they equal policy.TOOL_CATALOGUE[domain])."""
    server = MCPServer(name=f"corpai-{domain}", instructions=f"Kestrelbyte {domain} tools. Every call needs a "
                       "tool token; results carry a sensitivity label.")
    for fn in _tools(domain):
        server.add_tool(fn, name=fn.__name__, description=(fn.__doc__ or "").strip() or None)
    return server


def http_app(server: MCPServer):
    """The Starlette app of one server: stateless streamable HTTP with JSON responses."""
    return server.streamable_http_app(stateless_http=True, json_response=True, streamable_http_path=ENDPOINT_PATH)


def mount_all(app: Any, domains: Iterable[str] = DOMAINS):
    """Mount every domain server on `app` (Starlette or FastAPI) at /mcp/<domain>. Returns an async
    context manager running their session managers (each can run only once: call mount_all once
    per app)."""
    servers = {d: build_server(d) for d in domains}
    for d, server in servers.items():
        app.mount(f"{MOUNT_PREFIX}/{d}", http_app(server))

    @asynccontextmanager
    async def running() -> AsyncIterator[dict[str, MCPServer]]:
        async with AsyncExitStack() as stack:
            for server in servers.values():
                await stack.enter_async_context(server.session_manager.run())
            yield servers

    return running()


def run_standalone(domain: str, host: str = "127.0.0.1", port: int = 8101) -> None:
    """Serve one domain on its own at http://host:port/mcp/<domain>/mcp (for development).

    psycopg async needs a selector event loop on Windows (docs/ENGINEERING.md), hence no bare uvicorn CLI."""
    import uvicorn
    from starlette.applications import Starlette

    app = Starlette()
    lifespan_cm = mount_all(app, [domain])

    @asynccontextmanager
    async def lifespan(_app):
        async with lifespan_cm:
            yield

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, loop="none", log_level="info"))
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)


__all__ = ["DOMAINS", "AuthContext", "ToolDenied", "authenticate", "build_server", "http_app", "mount_all",
           "run_standalone"]
