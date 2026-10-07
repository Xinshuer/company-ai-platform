"""authenticate(): every way to call a tool without the right token, user or agent is refused and
audited, and token text never shows up in results, audit rows, logs or exceptions (§11, 二.4)."""

from __future__ import annotations

import logging
import time

import httpx2
import jwt
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from corpai.identity import directory, keys, tokens
from corpai.ids import new_id
from corpai.mcp_client import McpCallError, McpClient
from corpai.mcp_servers import build_server
from tests.mcp.support import audit_rows, owner_exec

pytestmark = [pytest.mark.anyio, pytest.mark.security]

URL = "http://127.0.0.1:8000/mcp/crm/mcp"


async def raw_call(app, url: str, tool: str, args: dict, headers: dict | None = None):
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000",
                                  headers=headers or {}) as http:
        async with Client(streamable_http_client(url, http_client=http), cache=None) as c:
            return await c.call_tool(tool, args)


def text_of(result) -> str:
    return "".join(p.text for p in result.content if getattr(p, "type", None) == "text")


def deny_rows(run_id: str) -> list[dict]:
    return [r for r in audit_rows(run_id) if r["event"] == "tool_call" and r["decision"] == "deny"]


def assert_no_token_anywhere(token: str, *texts: str) -> None:
    sig = token.rsplit(".", 1)[-1]
    for t in texts:
        assert token not in t and sig not in t


async def test_missing_token_is_denied_and_audited(app):
    res = await raw_call(app, URL, "search_accounts", {})
    assert res.is_error and "denied: a valid tool token is required" in text_of(res)
    from tests.mcp.support import owner_rows

    rows = owner_rows("SELECT * FROM audit.log WHERE event = 'tool_call' AND reason = 'token_missing'"
                      " AND action = 'crm.search_accounts'")
    assert rows and rows[-1]["decision"] == "deny" and rows[-1]["actor"] == "anonymous"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer not-a-jwt", "eyJhbGciOi"])
async def test_malformed_authorization_is_denied(app, header):
    res = await raw_call(app, URL, "search_accounts", {}, {"Authorization": header})
    assert res.is_error and "denied:" in text_of(res)


async def test_wrong_audience_is_denied(app, personas):
    run_id = new_id("run")
    token = tokens.mint_tool_token(personas["sales_staff"], domain="code", agent="sales_profile", run_id=run_id,
                                   tools=["search_accounts"])
    res = await raw_call(app, URL, "search_accounts", {}, {"Authorization": f"Bearer {token}"})
    assert res.is_error and "invalid or expired tool token" in text_of(res)
    assert_no_token_anywhere(token, text_of(res))


async def test_expired_token_is_denied(app, personas):
    now = int(time.time())
    claims = {"iss": "corpai", "aud": "mcp:crm", "sub": "u0011", "act": {"sub": "agent:sales_profile"},
              "scope": "search_accounts", "run_id": new_id("run"), "jti": new_id("jti"), "iat": now - 120,
              "exp": now - 60}
    token = jwt.encode(claims, keys.load_key(keys.TOOL), algorithm="HS256")
    res = await raw_call(app, URL, "search_accounts", {}, {"Authorization": f"Bearer {token}"})
    assert res.is_error and "invalid or expired tool token" in text_of(res)
    from tests.mcp.support import owner_rows

    rows = owner_rows("SELECT details::text AS d FROM audit.log WHERE reason = 'token_expired' ORDER BY seq DESC LIMIT 1")
    assert rows
    assert_no_token_anywhere(token, text_of(res), rows[0]["d"])


async def test_forged_signature_is_denied(app):
    now = int(time.time())
    claims = {"iss": "corpai", "aud": "mcp:crm", "sub": "u0001", "act": {"sub": "agent:sales_profile"},
              "scope": "search_accounts", "run_id": "run_x", "jti": "jti_x", "iat": now, "exp": now + 60}
    token = jwt.encode(claims, b"not-the-tool-key-" * 4, algorithm="HS256")
    res = await raw_call(app, URL, "search_accounts", {}, {"Authorization": f"Bearer {token}"})
    assert res.is_error and "invalid or expired tool token" in text_of(res)


async def test_out_of_scope_token_is_denied(app, personas):
    run_id = new_id("run")
    token = tokens.mint_tool_token(personas["sales_staff"], domain="crm", agent="sales_profile", run_id=run_id,
                                   tools=["search_accounts"])
    res = await raw_call(app, URL, "get_account", {"account_id": "A-0002"}, {"Authorization": f"Bearer {token}"})
    assert res.is_error and "does not cover this tool" in text_of(res)
    rows = deny_rows(run_id)
    assert rows and rows[0]["reason"] == "not_in_scope" and rows[0]["actor"] == "u0011"
    assert rows[0]["act_chain"] == ["agent:sales_profile"]
    assert_no_token_anywhere(token, *(r["details"] for r in rows))


async def test_token_for_one_call_is_not_a_session(app, personas):
    """A token minted for a read tool cannot be replayed against a write tool of the same domain."""
    token = tokens.mint_tool_token(personas["sales_staff"], domain="crm", agent="sales_profile",
                                   run_id=new_id("run"), tools=["get_account"])
    res = await raw_call(app, URL, "send_email", {"account_id": "A-0002", "to": "x@y.example", "subject": "s",
                                                  "body": "b"}, {"Authorization": f"Bearer {token}"})
    assert res.is_error and "denied:" in text_of(res)


async def test_inactive_user_is_denied_even_with_a_valid_token(app, personas):
    rep = directory.get_principal_sync("u0103")  # an active sales rep, not a persona
    run_id = new_id("run")
    token = tokens.mint_tool_token(rep, domain="crm", agent="sales_profile", run_id=run_id,
                                   tools=["search_accounts"])
    owner_exec("UPDATE core.users SET active = false WHERE user_id = 'u0103'")
    try:
        res = await raw_call(app, URL, "search_accounts", {}, {"Authorization": f"Bearer {token}"})
    finally:
        owner_exec("UPDATE core.users SET active = true WHERE user_id = 'u0103'")
    assert res.is_error and "inactive or unknown" in text_of(res)
    rows = deny_rows(run_id)
    assert rows and rows[0]["reason"] == "user_inactive_or_unknown"


async def test_client_refuses_to_mint_for_an_inactive_principal(app, personas):
    import dataclasses

    gone = dataclasses.replace(personas["sales_staff"], active=False)
    env = await McpClient(asgi_app=app).call(principal=gone, agent="sales_profile", run_id=new_id("run"),
                                             domain="crm", tool="search_accounts", args={})
    assert not env.ok and env.error == "tool_denied" and env.data is None


@pytest.mark.parametrize("persona,agent,domain,tool,args", [
    ("sales_staff", "qa", "crm", "send_email",                  # qa does not hold write tools
     {"account_id": "A-0002", "to": "esila.beernink@havenmeer-distributie.example", "subject": "s", "body": "b"}),
    ("sales_staff", "sales_web", "crm", "get_account", {"account_id": "A-0002"}),  # web agent: web only
    ("sales_staff", "qa", "web", "search", {"company": "Havenmeer"}),  # web only through sales_web
    ("eng_staff", "sales_profile", "crm", "get_account", {"account_id": "A-0002"}),  # may not start the agent
    ("platform_admin", "qa", "crm", "search_accounts", {}),     # ops roles: no business tools
    ("auditor", "qa", "finance", "list_metrics", {}),
    ("sales_head", "report", "legal", "contracts_expiring", {}),  # legal tools are legal's and the CEO's
    ("sales_staff", "no_such_agent", "crm", "get_account", {"account_id": "A-0002"}),
])
async def test_agent_and_user_rules_deny(call, persona, agent, domain, tool, args):
    run_id = new_id("run")
    env = await call(persona, agent, domain, tool, run_id=run_id, **args)
    assert not env.ok and env.error == "tool_denied" and env.data is None
    assert env.denied and env.denied[0]["id"] == f"{domain}.{tool}"
    rows = deny_rows(run_id)
    assert rows and rows[0]["action"] == f"{domain}.{tool}" and rows[0]["reason"] == "policy_deny"


async def test_in_process_call_without_http_has_no_identity(seeded):
    server = build_server("crm")
    async with Client(server, cache=None) as c:
        res = await c.call_tool("search_accounts", {})
    assert res.is_error and "a valid tool token is required" in text_of(res)


async def test_allowed_call_is_audited_with_jti_not_token(call):
    run_id = new_id("run")
    env = await call("sales_staff", "sales_profile", "crm", "get_account", run_id=run_id, account_id="A-0002")
    assert env.ok
    rows = [r for r in audit_rows(run_id) if r["event"] == "tool_call"]
    assert len(rows) == 1
    r = rows[0]
    assert r["decision"] == "allow" and r["actor"] == "u0011" and r["level"] == 3
    assert r["resource"] == "crm.account:A-0002" and r["act_chain"] == ["agent:sales_profile"]
    assert '"jti": "jti_' in r["details"]
    assert "eyJ" not in r["details"] and "Bearer" not in r["details"]
    assert "Havenmeer" not in r["details"]  # no business data in audit


async def test_token_never_in_client_exceptions_or_logs(personas, caplog):
    caplog.set_level(logging.DEBUG)
    client = McpClient("http://127.0.0.1:9")  # nothing listens there
    with pytest.raises(McpCallError) as ei:
        await client.call(principal=personas["sales_staff"], agent="sales_profile", run_id=new_id("run"),
                          domain="crm", tool="search_accounts", args={})
    chain = []
    e: BaseException | None = ei.value
    while e is not None:
        chain.append(repr(e))
        e = e.__cause__ or e.__context__
    for text in [*chain, caplog.text]:
        assert "eyJ" not in text and "Bearer" not in text
