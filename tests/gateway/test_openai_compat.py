"""POST /v1/chat/completions: gateway-token auth, principal re-resolution, routing by token floor."""

from __future__ import annotations

import time

import httpx2
import jwt
import openai
import pytest
from fastapi import FastAPI

from corpai.db import pg as pgmod
from corpai.gateway import openai_compat
from corpai.identity import directory, keys, tokens
from corpai.ids import new_id
from corpai.types import Level

pytestmark = [pytest.mark.anyio, pytest.mark.security]

PUBLIC_Q = "What is deferred revenue?"


def _add_user(*, active: bool = True, dept: str = "legal") -> str:
    uid = new_id("u")
    with pgmod.connect_owner() as c:
        c.execute("""INSERT INTO core.users (user_id, name, email, dept, role, title, clearance, active)
                     VALUES (%s, %s, %s, %s, 'employee', 'Counsel', 4, %s)""",
                  (uid, f"Test {uid}", f"{uid}@abctech.example", dept, active))
    return uid


@pytest.fixture
def user(pg):
    return directory.get_principal_sync(_add_user())


@pytest.fixture
def app(gw):
    a = FastAPI()
    a.include_router(openai_compat.router)
    a.state.gateway = gw
    return a


@pytest.fixture
async def client(app):
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://gw.test") as c:
        yield c


def _token(p, *, floor=Level.L1, agent="clause_check", run_id=None) -> str:
    return tokens.mint_gateway_token(p, agent=agent, run_id=run_id or new_id("run"), floor=floor)


async def _post(client, token, *, content=PUBLIC_Q, **body):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    payload = {"model": "whatever", "messages": [{"role": "user", "content": content}], **body}
    return await client.post("/v1/chat/completions", json=payload, headers=headers)


def _forged(p, **override) -> str:
    now = int(time.time())
    claims = {"iss": "corpai", "aud": "gateway", "sub": p.user_id, "act": {"sub": "agent:clause_check"},
              "run_id": "r", "floor": 1, "jti": "j", "iat": now, "exp": now + 60}
    key = override.pop("_key", None) or keys.load_key(keys.GATEWAY)
    claims.update(override)
    return jwt.encode(claims, key, algorithm="HS256")


async def test_rejects_missing_and_bad_tokens(client, fake, user):
    tool_token = tokens.mint_tool_token(user, domain="legal", agent="legal_review", run_id="r",
                                        tools=["get_contract"])
    bad = {
        "missing": None,
        "empty": "",
        "garbage": "not-a-jwt",
        "wrong_key": _forged(user, _key=b"x" * 32),
        "expired": _forged(user, iat=int(time.time()) - 600, exp=int(time.time()) - 300),
        "wrong_aud": _forged(user, aud="mcp:legal"),
        "tool_token": tool_token,
        "alg_none": jwt.encode({"sub": user.user_id, "aud": "gateway"}, None, algorithm="none")
        if "none" in jwt.algorithms.get_default_algorithms() else "x.y.",
        "bad_floor": _forged(user, floor=7),
        "unknown_user": _forged(user, sub="u_nobody"),
    }
    bodies = set()
    for name, tok in bad.items():
        r = await _post(client, tok)
        assert r.status_code == 401, name
        assert r.headers.get("www-authenticate") == "Bearer"
        if tok:
            assert tok not in r.text
        bodies.add(r.text)
    assert len(bodies) == 1  # identical answer whatever was wrong
    r = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]},
                          headers={"Authorization": f"Basic {_token(user)}"})
    assert r.status_code == 401
    assert fake.calls == []


async def test_deactivated_user_rejected_immediately(client, fake, pg):
    uid = _add_user()
    tok = _token(directory.get_principal_sync(uid))
    assert (await _post(client, tok)).status_code == 200
    with pgmod.connect_owner() as c:
        c.execute("UPDATE core.users SET active = false WHERE user_id = %s", (uid,))
    r = await _post(client, tok)
    assert r.status_code == 401
    assert len(fake.calls) == 1


@pytest.mark.parametrize("floor,content,route,level", [
    (Level.L1, PUBLIC_Q, "external", 1),
    (Level.L2, PUBLIC_Q, "external", 2),
    (Level.L3, PUBLIC_Q, "local", 3),
    (Level.L4, PUBLIC_Q, "local", 4),
    (Level.L1, "Check IBAN DE89 3704 0044 0532 0130 00", "local", 3),   # content still upgrades
])
async def test_routes_by_token_floor(client, fake, user, floor, content, route, level):
    run_id = new_id("run")
    r = await _post(client, _token(user, floor=floor, run_id=run_id), content=content,
                    model="external-please-gpt")  # the body's model is ignored
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion" and body["model"] == "deepseek-flash"
    msg = body["choices"][0]["message"]
    assert msg["role"] == "assistant" and msg["content"].startswith(f"[fake/{route}]")
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] > 0
    assert body["corpai"]["route"] == route and body["corpai"]["level"] == level
    call = fake.calls[-1]
    assert call["purpose"] == "compat.clause_check" and call["route"] == route
    with pgmod.connect_owner() as c:
        row = c.execute("SELECT * FROM audit.log WHERE run_id = %s AND event = 'model_call'", (run_id,)).fetchone()
    assert row["actor"] == user.user_id and row["act_chain"] == ["agent:clause_check"]


async def test_json_mode_and_tool_calls(client, fake, user):
    fake.register("compat.", lambda messages, *, route, json_mode: {
        "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                       "function": {"name": "lookup", "arguments": "{}"}}]} if not json_mode
        else '{"ok": true}')
    r = await _post(client, _token(user), response_format={"type": "json_object"})
    assert r.json()["choices"][0]["message"]["content"] == '{"ok": true}' and fake.calls[-1]["json_mode"]
    r = await _post(client, _token(user), tools=[{"type": "function", "function": {"name": "lookup",
                                                                                  "parameters": {}}}])
    choice = r.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls" and choice["message"]["tool_calls"][0]["function"]["name"] == "lookup"


@pytest.mark.parametrize("body,status", [
    ({"stream": True}, 400),
    ({"messages": []}, 400),
    ({"messages": "hello"}, 400),
    ({"max_tokens": -1}, 400),
    ({"temperature": 5}, 400),
])
async def test_bad_bodies(client, fake, user, body, status):
    tok = _token(user)
    payload = {"messages": [{"role": "user", "content": PUBLIC_Q}], **body}
    r = await client.post("/v1/chat/completions", json=payload, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == status
    assert fake.calls == []


async def test_invalid_json_body(client, user):
    r = await client.post("/v1/chat/completions", content=b"{not json",
                          headers={"Authorization": f"Bearer {_token(user)}", "Content-Type": "application/json"})
    assert r.status_code == 400


async def test_local_down_gives_503(client, fake, user, set_switches):
    set_switches(True, False)
    r = await _post(client, _token(user, floor=Level.L3))
    assert r.status_code == 503 and r.json()["error"]["type"] == "local_unavailable"
    assert fake.calls == []


async def test_unchanged_openai_sdk_client_works(app, fake, user):
    """What M2 relies on: the stock OpenAI SDK with the gateway token as its API key."""
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http:
        client = openai.AsyncOpenAI(api_key=_token(user, floor=Level.L3), base_url="http://gw.test/v1",
                                    http_client=http, max_retries=0)
        resp = await client.chat.completions.create(model="deepseek-chat",
                                                    messages=[{"role": "user", "content": PUBLIC_Q}])
    assert resp.choices[0].message.content.startswith("[fake/local]")
    assert resp.usage.total_tokens > 0


async def test_refused_secret_maps_to_400_without_echoing_it(client, user, monkeypatch):
    from corpai.gateway import gateway as gwmod

    monkeypatch.setattr(gwmod, "scrub_secrets", lambda text: (text, 0))  # simulate a scrubber miss
    r = await _post(client, _token(user), content="token sk-abcdefghijklmnopqrstuvwxyz0123")
    assert r.status_code == 400 and r.json()["error"]["type"] == "secret_detected"
    assert "sk-abc" not in r.text
