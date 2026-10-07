"""Rule 1 (secrets scrubbed before any backend) and the API key handling of the live backend.

The live backend is exercised against an httpx2.MockTransport standing in for the DeepSeek API,
so nothing leaves the machine and no real key is ever used (conftest removes DEEPSEEK_API_KEY;
these tests set a dummy one with monkeypatch).
"""

from __future__ import annotations

import json

import httpx2
import pytest

from corpai.db import pg as pgmod
from corpai.gateway import (
    BackendError,
    ChatRequest,
    EgressDenied,
    MissingApiKey,
    OpenAICompatBackend,
    RetryConfig,
)
from corpai.types import Level, Route

pytestmark = [pytest.mark.anyio, pytest.mark.security]

DUMMY_KEY = "sk-dummyTESTkey0123456789abcdefXYZ"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MDAzMSJ9.c2lnbmF0dXJlLXRlc3Q"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0123456789abcdef\n-----END RSA PRIVATE KEY-----"


def req(principal, text, *, run_id, hwm=Level.L1, purpose="qa.answer") -> ChatRequest:
    return ChatRequest(messages=[{"role": "system", "content": "Be brief."}, {"role": "user", "content": text}],
                       principal=principal, hwm=hwm, purpose=purpose, run_id=run_id, agent="qa")


def _everything_written(run_id: str) -> str:
    """All audit, trace and egress text the platform kept for this run, as one string."""
    with pgmod.connect_owner() as c:
        parts = [json.dumps(r, default=str) for r in c.execute(
            "SELECT seq, actor, act_chain, event, action, resource, decision, reason, route, level, run_id, details "
            "FROM audit.log WHERE run_id = %s", (run_id,)).fetchall()]
        parts += [json.dumps(r, default=str) for r in c.execute(
            "SELECT * FROM trace.spans WHERE run_id = %s", (run_id,)).fetchall()]
        parts += [json.dumps(r, default=str) for r in c.execute(
            "SELECT * FROM ops.egress_log WHERE run_id = %s", (run_id,)).fetchall()]
    return "\n".join(parts)


async def test_secrets_scrubbed_before_backend(gw, fake, staff, run_id, egress_rows, audit_rows):
    text = (f"Use api key {DUMMY_KEY} and password=hunter2xyz with token {FAKE_JWT}\n{PEM}\n"
            "What is deferred revenue?")
    res = await gw.chat(req(staff, text, run_id=run_id))
    seen = json.dumps(fake.calls[-1]["messages"])
    for secret in (DUMMY_KEY, "hunter2xyz", FAKE_JWT, "MIIEowIBAAKCAQEA"):
        assert secret not in seen
    assert "[REDACTED:" in seen
    assert res.route == Route.EXTERNAL  # once scrubbed, the rest is public
    (row,) = audit_rows(run_id)
    assert row["details"]["redactions"] >= 4
    written = _everything_written(run_id)
    for secret in (DUMMY_KEY, "hunter2xyz", FAKE_JWT, "MIIEowIBAAKCAQEA"):
        assert secret not in written


async def test_secrets_scrubbed_in_parts_and_tool_args(gw, fake, staff, run_id):
    r = ChatRequest(messages=[
        {"role": "user", "content": [{"type": "text", "text": f"key {DUMMY_KEY}"}]},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": json.dumps({"auth": f"Bearer {FAKE_JWT}"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ], principal=staff, hwm=Level.L1, purpose="qa.answer", run_id=run_id, agent="qa")
    await gw.chat(r)
    seen = json.dumps(fake.calls[-1]["messages"])
    assert DUMMY_KEY not in seen and FAKE_JWT not in seen


async def test_caller_messages_not_mutated(gw, staff, run_id):
    msgs = [{"role": "user", "content": f"key {DUMMY_KEY}"}]
    await gw.chat(ChatRequest(messages=msgs, principal=staff, hwm=Level.L1, purpose="qa.a", run_id=run_id, agent="qa"))
    assert msgs[0]["content"] == f"key {DUMMY_KEY}"


async def test_api_key_never_written_in_fake_mode(gw, staff, run_id, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY)
    await gw.chat(req(staff, "What is deferred revenue?", run_id=run_id))
    await gw.chat(req(staff, "Contact anna.k@nordhafen-logistik.example", run_id=run_id))
    assert DUMMY_KEY not in _everything_written(run_id)


# ---- live backend against a mock API ----------------------------------------------------------

def _ok_body(content="mock answer", model="deepseek-flash-0601"):
    return {"id": "x", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


class MockApi:
    def __init__(self, responses: list[tuple[int, dict]]):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status, body = self.responses.pop(0) if self.responses else (200, _ok_body())
        return httpx2.Response(status, json=body)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handler)


@pytest.fixture
def live_gw(make_gw):
    def _make(api: MockApi, **kw):
        return make_gw(backend=OpenAICompatBackend(transport=api.transport()),
                       retry=RetryConfig(max_attempts=3, base_delay_s=0.01, max_delay_s=0.02), **kw)
    return _make


async def test_live_backend_request_shape_and_key_at_call_time(live_gw, staff, run_id, monkeypatch):
    api = MockApi([])
    gw = live_gw(api)
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY)
    res = await gw.chat(req(staff, "What is deferred revenue?", run_id=run_id))
    assert res.text == "mock answer" and res.usage == {"prompt_tokens": 11, "completion_tokens": 7,
                                                       "total_tokens": 18}
    (sent,) = api.requests
    assert sent.url.host == "api.deepseek.com" and sent.url.scheme == "https"
    assert sent.headers["authorization"] == f"Bearer {DUMMY_KEY}"
    body = json.loads(sent.content)
    assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}
    # the key lives nowhere on the backend or gateway objects
    for obj in (gw, gw.backend, gw.config):
        assert DUMMY_KEY not in repr(obj) and DUMMY_KEY not in repr(vars(obj))
    # and nowhere the platform writes
    assert DUMMY_KEY not in _everything_written(run_id)
    # a key change takes effect on the next call (read at call time, not cached)
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY + "2")
    await gw.chat(req(staff, "What is deferred revenue?", run_id=run_id))
    assert api.requests[-1].headers["authorization"] == f"Bearer {DUMMY_KEY}2"


async def test_live_auth_error_does_not_echo_key(live_gw, staff, run_id, monkeypatch):
    """DeepSeek-style 401 bodies quote the key; our error must not."""
    api = MockApi([(401, {"error": {"message": f"Authentication Fails, Your api key: {DUMMY_KEY} is invalid",
                                    "type": "authentication_error"}})])
    gw = live_gw(api)
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY)
    with pytest.raises(BackendError) as ei:
        await gw.chat(req(staff, "What is deferred revenue?", run_id=run_id))
    err = ei.value
    assert DUMMY_KEY not in str(err) and DUMMY_KEY not in repr(err)
    assert err.__cause__ is None and err.__context__ is None
    assert err.status == 401 and len(api.requests) == 1  # 401 is not retried
    assert DUMMY_KEY not in _everything_written(run_id)


async def test_live_retries_429_then_succeeds(live_gw, staff, run_id, monkeypatch):
    api = MockApi([(429, {"error": {"message": "slow down"}}), (500, {"error": {"message": "oops"}})])
    gw = live_gw(api)
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY)
    res = await gw.chat(req(staff, "What is deferred revenue?", run_id=run_id))
    assert res.text == "mock answer" and len(api.requests) == 3


async def test_live_missing_key_names_variable_only(live_gw, staff, run_id, monkeypatch):
    api = MockApi([])
    gw = live_gw(api)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(MissingApiKey) as ei:
        await gw.chat(req(staff, "What is deferred revenue?", run_id=run_id))
    assert "DEEPSEEK_API_KEY" in str(ei.value)
    assert api.requests == []


async def test_live_unlisted_host_refused_before_sending(live_gw, staff, run_id, monkeypatch):
    api = MockApi([])
    gw = live_gw(api, local={"base_url": "https://llm.attacker.example"})
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY)
    with pytest.raises(EgressDenied):
        await gw.chat(req(staff, "Contact anna.k@nordhafen-logistik.example", run_id=run_id))
    assert api.requests == []


async def test_live_backend_refuses_l3_to_external_destination(staff, monkeypatch):
    """Even called directly (bypassing the gateway), the backend will not send L3 to model.external."""
    from corpai.gateway import BackendCall, load_config

    api = MockApi([])
    backend = OpenAICompatBackend(transport=api.transport())
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY)
    call = BackendCall(route=Route.EXTERNAL, level=Level.L3, purpose="x", messages=[{"role": "user", "content": "x"}],
                       json_mode=False, max_tokens=10, temperature=0, tools=None)
    with pytest.raises(EgressDenied):
        await backend.complete(call, load_config().route(Route.EXTERNAL))
    assert api.requests == []


async def test_live_plain_http_refused(live_gw, staff, run_id, monkeypatch):
    api = MockApi([])
    gw = live_gw(api, external={"base_url": "http://api.deepseek.com"})
    monkeypatch.setenv("DEEPSEEK_API_KEY", DUMMY_KEY)
    with pytest.raises(EgressDenied):
        await gw.chat(req(staff, "What is deferred revenue?", run_id=run_id))
    assert api.requests == []
