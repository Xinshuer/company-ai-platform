"""HR graphs wired end to end (decision D6, D7; EU AI Act Annex III 4(a); GDPR Art. 22), through the API.

Properties proved here:
- screening: evidence only, every model call local and tool-less, no score / rank / recommendation
  anywhere in the run, the evidence or the stored rows; the injected résumé changes nothing;
- the decision is a person's: it needs the right role, the caller's own waiting thread, the right
  candidate and a reason; refusals are audited; the reason never enters graph state or checkpoints;
- a counterfactual pair (name, gender, age differ) gives identical model input and identical
  evidence through the whole graph;
- hiring managers see only their own requisitions' candidates; everyone else is denied explicitly;
- headcount: L2 public part external, L3 budget part local and only for those entitled, n<5 hidden.
"""

from __future__ import annotations

import json

import pytest

from corpai.agents import router
from corpai.agents._rule_of_two import graph_caps, violations
from tests.api.support import owner_rows
from tests.m4.conftest import audit_rows, keys_of, post_run

pytestmark = pytest.mark.anyio

FORBIDDEN_KEYS = {"score", "total_score", "rank", "ranking", "recommendation", "fit", "rating"}
REASON = "Strong routing background; checking PostgreSQL depth in the interview."


def _screen_calls(fake) -> list[dict]:
    return [c for c in fake.calls if c["purpose"] == "hr.screen"]


async def _decide(client, run: dict, decision: str, reason: str, candidate: str | None = None):
    return await client.post("/api/hr/screenings/decision", json={
        "thread_id": run["thread_id"], "candidate_id": candidate or run["output"]["data"]["candidate_id"],
        "decision": decision, "reason": reason})


async def test_screening_is_local_evidence_without_any_score(app_client, as_persona, fake_llm):
    await as_persona("hr_staff")
    run = await post_run(app_client, "CAND-001", app="hr_screening")
    data = run["output"]["data"]
    assert run["status"] == "done" and run["hwm"] == 4 and run["route"] == "local"
    assert data["awaiting_decision"] is True and data["decision"] is None and data["evidence_id"]
    assert data["items"] and all(i["verdict"] in ("met", "not_met", "unknown") for i in data["items"])
    assert not (keys_of(run) & FORBIDDEN_KEYS)
    calls = _screen_calls(fake_llm)
    assert len(calls) == len(data["items"])
    assert all(c["route"] == "local" and c["level"] >= 3 and c["tools"] is None for c in calls)
    assert "Kowalczyk" not in json.dumps([c["messages"] for c in calls])  # identity removed before the model
    assert data["protected_removed"] and set(data["protected_removed"]) >= {"gender", "photo"}
    assert not [e for e in run["events"] if e["t"] == "tool"]  # the screening node holds no tool at all
    (row,) = owner_rows("SELECT evidence FROM hr.screening_evidence WHERE evidence_id = %s", (data["evidence_id"],))
    assert not (keys_of(row["evidence"]) & FORBIDDEN_KEYS)
    # nothing is decided until a person decides
    assert not owner_rows("SELECT 1 FROM hr.screenings WHERE candidate_id = 'CAND-001'")


async def test_decision_needs_reason_role_own_thread_and_the_right_candidate(app_client, as_persona, personas,
                                                                              clone_candidate):
    cid = clone_candidate("CAND-003")  # R-01, recruiter hr_staff
    await as_persona("hr_staff")
    run = await post_run(app_client, cid, app="hr_screening")
    assert run["output"]["data"]["awaiting_decision"] is True

    blank = await _decide(app_client, run, "reject", "   ")
    assert blank.status_code == 400 and blank.json()["error"]["code"] == "bad_request"
    bad = await _decide(app_client, run, "auto_reject", REASON)
    assert bad.status_code == 400
    other = await _decide(app_client, run, "advance", REASON, candidate="CAND-004")
    assert other.status_code == 400
    denials = owner_rows("SELECT decision, reason FROM audit.log WHERE event = 'hr_decision' AND actor = %s"
                         " AND resource = %s", (personas["hr_staff"].user_id, f"hr.candidate:{cid}"))
    assert {d["reason"] for d in denials} >= {"reason_required", "invalid_decision"}
    assert all(d["decision"] == "deny" for d in denials)
    assert not owner_rows("SELECT 1 FROM hr.screenings WHERE candidate_id = %s", (cid,))

    await as_persona("hr_head")  # another person's thread: unknown to them, even for the head of HR
    r = await _decide(app_client, run, "advance", REASON)
    assert r.status_code == 404
    await as_persona("eng_staff")  # not HR: may not start the app at all
    r = await _decide(app_client, run, "advance", REASON)
    assert r.status_code in (403, 404)

    await as_persona("hr_staff")
    ok = await _decide(app_client, run, "advance", REASON)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["screening_id"].startswith("scr_") and body["run"]["status"] == "done"
    assert body["run"]["output"]["data"]["decision"]["decision"] == "advance"
    assert REASON not in json.dumps(body)  # the reason is not echoed into the run
    (row,) = owner_rows("SELECT decision, reason, decided_by FROM hr.screenings WHERE screening_id = %s",
                        (body["screening_id"],))
    assert row["decision"] == "advance" and row["reason"] == REASON and row["decided_by"] == personas["hr_staff"].user_id
    allow = owner_rows("SELECT * FROM audit.log WHERE event = 'hr_decision' AND decision = 'allow'"
                       " AND resource = %s", (f"hr.candidate:{cid}",))
    assert allow and REASON not in json.dumps(allow, default=str)
    # the reason (personal, L4) never reaches the thread's checkpoints
    for table in ("checkpoint_blobs", "checkpoint_writes"):
        n = owner_rows(f"SELECT count(*) AS n FROM lg.{table} WHERE thread_id = %s AND "
                       "position(convert_to(%s, 'UTF8') in blob) > 0", (run["thread_id"], REASON))[0]["n"]
        assert n == 0, table
    stored = owner_rows("SELECT count(*) AS n FROM lg.checkpoint_writes WHERE thread_id = %s AND "
                        "position(convert_to(%s, 'UTF8') in blob) > 0", (run["thread_id"], body["screening_id"]))
    assert stored[0]["n"] > 0, "the probe would find text that is in the checkpoints"
    again = await _decide(app_client, run, "reject", REASON)
    assert again.status_code == 400  # the thread no longer waits: one decision per screening run


async def test_hr_staff_cannot_screen_outside_their_requisitions(app_client, as_persona):
    await as_persona("hr_staff")  # recruiter of R-01..R-03; CAND-013 applied to R-04
    run = await post_run(app_client, "CAND-013", app="hr_screening")
    assert run["status"] == "error" and run["error"]["code"] == "forbidden"
    assert not run["output"]["data"]


async def test_counterfactual_pairs_get_identical_evidence_through_the_graph(app_client, as_persona, fake_llm):
    pairs = owner_rows("SELECT expected FROM synth.planted WHERE kind = 'counterfactual_pairs'")[0]["expected"]["pairs"]
    await as_persona("hr_head")
    for pair in pairs:
        items, inputs = [], []
        for cid in pair["candidates"]:
            fake_llm.calls.clear()
            run = await post_run(app_client, cid, app="hr_screening")
            items.append([(i["requirement_id"], i["verdict"], i["quote"]) for i in run["output"]["data"]["items"]])
            inputs.append([c["messages"] for c in _screen_calls(fake_llm)])
        assert inputs[0] == inputs[1], pair["pair"]
        assert items[0] == items[1], pair["pair"]


async def test_injected_resume_changes_nothing(app_client, as_persona, fake_llm):
    planted = owner_rows("SELECT expected FROM synth.planted WHERE kind = 'injected_resume'")[0]["expected"]
    await as_persona("hr_staff")
    run = await post_run(app_client, planted["candidate_id"], app="hr_screening")
    data = run["output"]["data"]
    assert data["embedded_instruction"] is True
    assert any(e["t"] == "notice" and e["code"] == "embedded_instruction" for e in run["events"])
    assert planted["attacker_address"] not in json.dumps(run, ensure_ascii=False)
    assert not [e for e in run["events"] if e["t"] in ("tool", "approval")]
    assert not owner_rows("SELECT 1 FROM ops.proposals WHERE run_id = %s", (run["run_id"],))


async def test_hiring_manager_sees_only_own_requisition(app_client, as_persona):
    await as_persona("sales_head")  # hiring manager of R-03
    data = (await app_client.get("/api/hr/candidates")).json()
    assert [r["req_id"] for r in data["requisitions"]] == ["R-03"]
    ids = {c["candidate_id"] for c in data["requisitions"][0]["candidates"]}
    expected = {r["candidate_id"] for r in owner_rows("SELECT candidate_id FROM hr.candidates WHERE req_id = 'R-03'")}
    assert ids == expected and not (keys_of(data) & ({"items", "verdict", "evidence"} | FORBIDDEN_KEYS))
    one = await app_client.get(f"/api/hr/candidates/{sorted(ids)[0]}")
    assert one.status_code == 200 and one.json()["trust"] == "untrusted" and one.json()["candidate"]["resume_text"]
    assert (await app_client.get("/api/hr/candidates/CAND-001")).status_code == 404  # R-01: not theirs
    r01 = (await app_client.get("/api/hr/candidates?req_id=R-01")).json()
    assert r01["requisitions"][0]["denied"] is True and not r01["requisitions"][0]["candidates"]
    run = await post_run(app_client, "my candidates", app="hr_candidates")
    assert [r["req_id"] for r in run["output"]["data"]["requisitions"]] == ["R-03"]
    assert run["route"] is None  # no model call in this app

    await as_persona("eng_staff")
    none = (await app_client.get("/api/hr/candidates")).json()
    assert none["requisitions"] == [] and none["denied"][0]["id"] == "hr.requisition:*"
    await as_persona("ceo")  # may list requisitions, not their candidates
    ceo = (await app_client.get("/api/hr/candidates")).json()
    assert ceo["requisitions"] and all(r["denied"] and not r["candidates"] for r in ceo["requisitions"])
    await as_persona("auditor")
    assert (await app_client.get("/api/hr/candidates")).status_code == 403


async def test_headcount_public_external_budget_local_and_suppressed(app_client, as_persona, fake_llm):
    await as_persona("hr_staff")
    staff = await post_run(app_client, "headcount", app="hr_headcount")
    d = staff["output"]["data"]
    assert staff["hwm"] == 2 and staff["route"] == "external" and d["note_route"]["route"] == "external"
    assert d["budget"]["allowed"] is False and d["budget"]["denied"]  # explicit, not skipped
    assert {x["dept"] for x in d["public"]["staff_denied"]}  # explicit per department
    await as_persona("hr_head")
    fake_llm.calls.clear()
    head = await post_run(app_client, "headcount", app="hr_headcount")
    d = head["output"]["data"]
    assert head["hwm"] == 3 and head["route"] == "local" and d["budget"]["allowed"] is True
    small = {s["dept"]: s for s in d["public"]["staff_by_dept"]}
    for dept in ("exec", "it", "legal"):  # under 5 people
        assert small[dept]["suppressed"] is True and small[dept]["employees"] is None
    (note,) = [c for c in fake_llm.calls if c["purpose"] == "hr.headcount_note"]
    assert note["route"] == "local" and "approved_budget" not in json.dumps(note["messages"])
    assert not any(ch.isdigit() for ch in head["output"]["text"])
    await as_persona("eng_staff")
    r = await app_client.post("/api/runs", json={"app": "hr_headcount", "input": "x"})
    assert r.status_code == 403


def test_rule_of_two_holds_for_the_hr_graphs():
    built = router.buildable_apps()
    for app in ("hr_headcount", "hr_screening", "hr_candidates"):
        g = built[app]()
        assert violations(g) == [], app
        assert all("external_action" not in c for c, _g in graph_caps(g).values()), app


def test_audit_rows_helper_is_scoped():
    assert audit_rows("run_does_not_exist") == []
