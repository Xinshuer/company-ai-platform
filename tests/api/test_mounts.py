"""The gateway endpoint and the MCP servers are mounted; serve uses the selector loop."""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.anyio


async def test_openai_compat_endpoint_is_mounted_and_needs_a_token(app_client):
    r = await app_client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


async def test_mcp_servers_are_mounted_and_refuse_without_a_token(app_client, personas):
    from corpai.mcp_client import McpClient

    # a tool call without a Bearer token: the server answers, and denies
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_account", "arguments": {"account_id": "A-0002"}}}
    r = await app_client.post("/mcp/crm/mcp", json=body,
                              headers={"accept": "application/json, text/event-stream"})
    assert r.status_code == 200
    assert "denied" in r.text
    # the in-process client path the graphs use works against this app
    app = app_client._transport.app  # httpx2.ASGITransport keeps the app
    env = await McpClient(asgi_app=app).call(principal=personas["sales_staff"], agent="qa", run_id="run_x",
                                             domain="crm", tool="search_accounts", args={"query": ""})
    assert env.ok and env.level >= 3


def test_serve_uses_the_selector_loop(monkeypatch):
    import uvicorn

    from corpai.cli import serve

    seen = {}

    class FakeServer:
        def __init__(self, config):
            seen["config"] = config

        async def serve(self):
            return None

    def fake_run(coro, *, loop_factory=None):
        seen["loop_factory"] = loop_factory
        coro.close()

    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(asyncio, "run", fake_run)
    assert serve.main(["--port", "8123"]) == 0
    cfg = seen["config"]
    assert seen["loop_factory"] is asyncio.SelectorEventLoop
    assert cfg.loop == "none" and cfg.lifespan == "on" and cfg.port == 8123 and cfg.host == "127.0.0.1"
