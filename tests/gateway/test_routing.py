"""Routing rules 2-5 and the record they leave (audit, trace, egress log)."""

from __future__ import annotations

import json

import pytest

from corpai.gateway import ChatRequest, ChatResult
from corpai.types import Level, Route

pytestmark = [pytest.mark.anyio, pytest.mark.security]

PUBLIC_Q = "What is deferred revenue?"
IBAN_Q = "What is deferred revenue? Please also check the payment to IBAN DE89 3704 0044 0532 0130 00"
CANARY = "KB-CANARY-0A1B2C3D"


def req(principal, text: str, *, hwm=Level.L1, run_id: str, purpose: str = "qa.answer", **kw) -> ChatRequest:
    return ChatRequest(messages=[{"role": "system", "content": "You are a helpful assistant."},
                                 {"role": "user", "content": text}],
                       principal=principal, hwm=hwm, purpose=purpose, run_id=run_id, agent="qa", **kw)


@pytest.mark.parametrize("hwm,text,route,level", [
    (Level.L1, PUBLIC_Q, Route.EXTERNAL, Level.L1),
    (Level.L2, PUBLIC_Q, Route.EXTERNAL, Level.L2),
    (Level.L3, PUBLIC_Q, Route.LOCAL, Level.L3),
    (Level.L4, PUBLIC_Q, Route.LOCAL, Level.L4),
    (Level.L1, "Summarise the ticket for anna.k@nordhafen-logistik.example", Route.LOCAL, Level.L3),
    (Level.L1, f"Explain {CANARY}", Route.LOCAL, Level.L4),
])
async def test_routing_table(gw, fake, staff, run_id, hwm, text, route, level):
    res = await gw.chat(req(staff, text, hwm=hwm, run_id=run_id))
    assert isinstance(res, ChatResult)
    assert (res.route, res.level) == (route, level)
    assert res.model == "deepseek-flash"
    assert fake.calls[-1]["route"] == str(route) and fake.calls[-1]["level"] == int(level)
    assert res.text.startswith(f"[fake/{route}]")


async def test_content_upgrade_iban_goes_local(gw, fake, staff, run_id, egress_rows, audit_rows):
    """A question that looks public but carries an IBAN is L3 and never leaves."""
    res = await gw.chat(req(staff, IBAN_Q, hwm=Level.L1, run_id=run_id))
    assert res.route == Route.LOCAL and res.level == Level.L3
    assert egress_rows(run_id) == []
    row = [r for r in audit_rows(run_id) if r["event"] == "model_call"][0]
    assert row["route"] == "local" and row["level"] == 3
    assert "iban" in row["details"]["findings"]


async def test_hwm_is_a_floor(gw, staff, run_id):
    """Public-looking text never lowers the level the graph already reached."""
    res = await gw.chat(req(staff, "hello", hwm=Level.L3, run_id=run_id))
    assert res.route == Route.LOCAL and res.level == Level.L3


@pytest.mark.parametrize("bad", [0, 5, 99, "L1", None, True, 2.5j])
async def test_invalid_hwm_fails_closed(gw, staff, run_id, bad):
    res = await gw.chat(req(staff, PUBLIC_Q, hwm=bad, run_id=run_id))
    assert res.route == Route.LOCAL and res.level == Level.L4


async def test_classifier_error_is_l4(gw, staff, run_id, monkeypatch):
    import corpai.gateway.gateway as gwmod
    from corpai.classify import Classification, Finding

    monkeypatch.setattr(gwmod, "classify_text", lambda text, **kw: Classification(
        Level.L4, (Finding("classifier_error", 0, 0, Level.L4),)))
    res = await gw.chat(req(staff, PUBLIC_Q, run_id=run_id))
    assert res.route == Route.LOCAL and res.level == Level.L4


async def test_content_in_any_message_part_counts(gw, staff, run_id):
    """Multi-part content and earlier turns are classified too, not only the last user string."""
    r = ChatRequest(messages=[
        {"role": "user", "content": [{"type": "text", "text": "Contact: anna.k@nordhafen-logistik.example"}]},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": PUBLIC_Q},
    ], principal=staff, hwm=Level.L1, purpose="qa.answer", run_id=run_id, agent="qa")
    res = await gw.chat(r)
    assert res.route == Route.LOCAL and res.level == Level.L3


async def test_dlp_reroutes_tool_payload(gw, fake, staff, run_id, egress_rows, audit_rows):
    """Messages are public, but a tool definition (also sent out) carries an IBAN: DLP catches it."""
    tools = [{"type": "function", "function": {
        "name": "pay", "description": "Pay to IBAN DE89 3704 0044 0532 0130 00",
        "parameters": {"type": "object", "properties": {}}}}]
    res = await gw.chat(req(staff, PUBLIC_Q, run_id=run_id, tools=tools))
    assert res.route == Route.LOCAL and res.level >= Level.L3
    assert fake.calls[-1]["route"] == "local"
    assert egress_rows(run_id) == []
    rows = audit_rows(run_id)
    assert [(r["event"], r["reason"]) for r in rows] == [("route_decision", "dlp_upgrade"),
                                                         ("model_call", "dlp_upgrade")]


async def test_external_down_falls_back_to_local(gw, fake, staff, run_id, set_switches, audit_rows, egress_rows):
    set_switches(False, True)
    res = await gw.chat(req(staff, PUBLIC_Q, run_id=run_id))
    assert res.route == Route.LOCAL and res.level == Level.L1
    assert fake.calls[-1]["route"] == "local"
    assert egress_rows(run_id) == []
    rows = audit_rows(run_id)
    assert [(r["event"], r["reason"], r["route"]) for r in rows] == [
        ("route_decision", "external_down_fallback", "local"), ("model_call", "external_down_fallback", "local")]


async def test_one_audit_row_per_call_without_text(gw, staff, run_id, audit_rows):
    marker = "Zebra-quokka-unique-phrase-4711"
    res = await gw.chat(req(staff, f"What is deferred revenue? {marker}", run_id=run_id))
    rows = audit_rows(run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "model_call" and row["seq"] == res.audit_seq
    assert row["actor"] == staff.user_id and row["act_chain"] == ["agent:qa"]
    assert (row["route"], row["level"], row["reason"], row["decision"]) == ("external", 1, "route_by_level", "allow")
    d = row["details"]
    assert d["purpose"] == "qa.answer" and d["total_tokens"] == res.usage["total_tokens"] > 0
    assert d["prompt_tokens"] > 0 and d["completion_tokens"] > 0
    dumped = json.dumps({k: (v if not isinstance(v, (bytes, memoryview)) else None) for k, v in row.items()},
                        default=str)
    assert marker not in dumped and "deferred revenue" not in dumped
    assert res.text not in dumped


async def test_egress_log_only_for_external(gw, staff, run_id, egress_rows):
    marker = "Ocelot-marker-889"
    await gw.chat(req(staff, f"What is deferred revenue? {marker}", run_id=run_id))
    rows = egress_rows(run_id)
    assert len(rows) == 1
    e = rows[0]
    assert (e["route"], e["level"], e["destination"], e["user_id"]) == ("external", 1, "model.external", staff.user_id)
    assert marker in e["payload"]
    import hashlib
    assert e["payload_sha256"] == hashlib.sha256(e["payload"].encode("utf-8")).hexdigest()

    local_run = run_id + "x"
    await gw.chat(req(staff, f"Prepare a summary {marker}", hwm=Level.L3, run_id=local_run))
    assert egress_rows(local_run) == []


async def test_canary_never_reaches_egress(gw, staff, run_id, egress_rows):
    """The canary scan of design-review 二.2: an L3 request carrying a canary, plus a mix of public
    calls in the same run, and the canary appears nowhere in the external egress log."""
    await gw.chat(req(staff, PUBLIC_Q, run_id=run_id))
    await gw.chat(req(staff, f"Contract excerpt: {CANARY} renewal terms", hwm=Level.L3, run_id=run_id))
    await gw.chat(req(staff, f"Explain {CANARY}", hwm=Level.L1, run_id=run_id))
    rows = egress_rows(None)
    assert any(r["run_id"] == run_id for r in rows)
    assert all(CANARY not in r["payload"] for r in rows)


async def test_trace_span_masked_by_level(gw, staff, run_id, span_rows):
    await gw.chat(req(staff, "Summarise the ticket for anna.k@nordhafen-logistik.example", run_id=run_id))
    l4_run = run_id + "-l4"
    await gw.chat(req(staff, f"Explain {CANARY}", run_id=l4_run))
    (span,) = span_rows(run_id)
    assert span["kind"] == "llm" and span["level"] == 3 and span["status"] == "ok"
    assert span["attrs"]["route"] == "local" and span["attrs"]["reason"] == "route_by_level"
    assert "anna.k@nordhafen-logistik.example" not in (span["input_text"] or "")
    (span4,) = span_rows(l4_run)
    assert span4["level"] == 4 and span4["input_text"] is None and span4["output_text"] is None


async def test_fake_records_calls_and_cites_docs(gw, fake, staff, run_id):
    r = req(staff, "Context: [doc:handbook-7] says trains are fine. Question: trains?", run_id=run_id,
            purpose="qa.answer", json_mode=True)
    res = await gw.chat(r)
    call = fake.calls[-1]
    assert call["purpose"] == "qa.answer" and call["json_mode"] is True
    assert call["messages"][1]["content"].startswith("Context:")
    body = json.loads(res.text)
    assert body["citations"] == ["[doc:handbook-7]"]


async def test_registered_responder_and_tool_calls(gw, fake, staff, run_id):
    fake.register("qa.", lambda messages, *, route, json_mode: "generic")
    fake.register("qa.special", lambda messages, *, route, json_mode: {
        "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                       "function": {"name": "f", "arguments": "{}"}}]})
    assert (await gw.chat(req(staff, PUBLIC_Q, run_id=run_id, purpose="qa.answer"))).text == "generic"
    res = await gw.chat(req(staff, PUBLIC_Q, run_id=run_id, purpose="qa.special.x"))
    assert res.tool_calls[0]["function"]["name"] == "f"


async def test_inactive_principal_refused(gw, fake, mkp, run_id, audit_rows):
    gone = mkp("u0099", active=False)
    with pytest.raises(PermissionError):
        await gw.chat(req(gone, PUBLIC_Q, run_id=run_id))
    assert fake.calls == []


@pytest.mark.parametrize("bad", [
    {"messages": []}, {"messages": "hi"}, {"messages": ["hi"]}, {"purpose": ""}, {"run_id": ""}, {"agent": ""},
])
async def test_malformed_requests_rejected(gw, fake, staff, run_id, bad):
    base = dict(messages=[{"role": "user", "content": "x"}], principal=staff, hwm=Level.L1, purpose="qa.a",
                run_id=run_id, agent="qa")
    base.update(bad)
    with pytest.raises(ValueError):
        await gw.chat(ChatRequest(**base))
    assert fake.calls == []


async def test_max_tokens_clamped(gw, fake, staff, run_id):
    await gw.chat(req(staff, PUBLIC_Q, run_id=run_id, max_tokens=10**9))
    assert fake.calls[-1]["max_tokens"] == gw.config.max_tokens_cap


@pytest.mark.parametrize("smuggled", [
    {"role": "user", "content": PUBLIC_Q, "DE89 3704 0044 0532 0130 00": "x"},          # in a dict key
    {"role": "user", "content": [{"type": "DE89 3704 0044 0532 0130 00", "text": PUBLIC_Q}]},  # in "type"
    {"role": "user", "content": PUBLIC_Q, "name": "anna.k@nordhafen-logistik.example"},
    {"role": "user", "content": PUBLIC_Q, "id": CANARY},
])
async def test_content_hidden_in_unusual_fields_is_classified(gw, fake, staff, run_id, egress_rows, smuggled):
    """Everything the backend receives is classified, not only the fields content usually sits in."""
    r = ChatRequest(messages=[smuggled], principal=staff, hwm=Level.L1, purpose="qa.answer", run_id=run_id,
                    agent="qa")
    res = await gw.chat(r)
    assert res.route == Route.LOCAL and res.level >= Level.L3
    assert egress_rows(run_id) == []
