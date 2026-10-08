"""Demo mode must refuse what the live API refuses (architecture §6, §8, §18).

ui/js/demo.js answers the API from fixtures when the page runs from disk. These tests run it
under Node (skipped when Node is not installed) and check the denials and fail-closed paths:
no persona → 401; apps, audit and admin outside the role → 403; someone else's trace → 404;
tier-3 self-approval → 403; a changed params hash or a missing reason → 400; local_down makes
confidential requests fail instead of going external; external_down moves L1/L2 to local.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("demo_harness.js")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def r() -> dict:
    proc = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _err(r, name, status, code=None):
    res = r[name]
    assert res["ok"] is False, f"{name} should have been refused: {res}"
    assert res["status"] == status, (name, res)
    if code:
        assert res["code"] == code, (name, res)


def test_nothing_without_a_persona(r):
    _err(r, "me_before_login", 401, "unauthenticated")
    _err(r, "runs_before_login", 401, "unauthenticated")
    _err(r, "me_after_logout", 401, "unauthenticated")


def test_apps_outside_the_role_are_forbidden(r):
    _err(r, "sales_legal_app", 403, "forbidden")


def test_audit_and_admin_are_role_gated(r):
    _err(r, "sales_audit", 403, "forbidden")
    _err(r, "sales_admin_get", 403, "forbidden")
    _err(r, "sales_admin_post", 403, "forbidden")
    _err(r, "admin_audit", 403, "forbidden")      # the platform admin does not read the audit log
    _err(r, "auditor_admin", 403, "forbidden")    # the auditor does not flip switches
    assert r["auditor_audit"]["ok"]


def test_audit_reads_are_themselves_audited(r):
    entries = r["auditor_audit"]["value"]["entries"]
    reads = [e for e in entries if e["event"] == "audit_read"]
    assert reads and reads[-1]["actor"] == "u0061"
    switches = [e for e in entries if e["event"] == "outage_switch" and e["actor"] == "u0060"]
    assert any(e["reason"] == "drill" for e in switches), "switch changes are audited with their reason"


def test_trace_of_someone_elses_run_is_not_found(r):
    assert r["own_trace"]["ok"]
    _err(r, "other_trace", 404, "not_found")


def test_shared_sample_answers_everyone_but_its_trace_is_nobodys(r):
    """issues-log SEC-1: a "*" recording was made as one persona; its trace shows what that persona retrieved."""
    assert r["shared_run"]["ok"] and r["shared_run"]["value"]["run_id"] == "run_w0shared"
    assert r["shared_trace_link"] is False
    _err(r, "shared_trace", 404, "not_found")


def test_staff_has_no_approval_inbox(r):
    assert r["sales_to_approve"] == {"ok": True, "value": []}


def test_tier3_self_approval_is_refused(r):
    _err(r, "self_approve_tier3", 403, "forbidden")


def test_approval_needs_reason_and_unchanged_hash(r):
    _err(r, "approve_no_reason", 400, "bad_request")
    _err(r, "approve_bad_hash", 400, "bad_request")
    ok = r["approve_ok"]
    assert ok["ok"]
    prop = ok["value"]["proposal"]
    assert prop["status"] == "executed" and prop["approver"]["user_id"] == "u0010"
    assert prop["can_approve"] is False
    _err(r, "approve_twice", 400, "bad_request")


def test_switches_need_a_reason_and_a_known_name(r):
    _err(r, "admin_no_reason", 400, "bad_request")
    _err(r, "admin_bad_name", 400, "bad_request")
    assert r["admin_local_down"]["value"]["local_down"] is True


def test_local_down_fails_closed_and_never_goes_external(r):
    for name in ("sales_qa_local_down", "ceo_qa_local_down"):
        run = r[name]["value"]
        assert run["status"] == "error" and run["error"]["code"] == "local_unavailable", name
        assert run["route"] == "local"
        assert not run["output"]["text"] and not run["output"]["citations"], "no answer leaks out"
        assert all(e.get("route") != "external" for e in run["events"] if e["t"] == "route")
    # an L2 request does not need the local route and keeps working
    eng = r["eng_qa_local_down"]["value"]
    assert eng["status"] == "done" and eng["route"] == "external" and eng["hwm"] <= 2


def test_external_down_moves_low_levels_to_local(r):
    run = r["eng_qa_external_down"]["value"]
    assert run["route"] == "local"
    assert any(e["t"] == "route" and e["reason"] == "external_down_fallback" for e in run["events"])


def test_user_level_can_raise_but_not_lower(r):
    raised = r["public_raised"]["value"]
    assert raised["hwm"] == 3 and raised["route"] == "local"
    base = r["sales_qa"]["value"]
    lowered = r["public_lowered"]["value"]
    assert lowered["hwm"] == base["hwm"] == 3 and lowered["route"] == "local"


# ---- review fixes: demo mirrors the live rules ------------------------------------------------------

def test_a_thread_keeps_its_level_in_demo(r):
    first = r["sales_qa"]["value"]
    assert first["hwm"] == 3 and first["route"] == "local"
    after = r["thread_public_after_l3"]["value"]
    assert after["thread_id"] == first["thread_id"]
    assert after["hwm"] >= 3 and after["route"] == "local", "a public question in an L3 thread stays local"
    assert all(e.get("route") != "external" for e in after["events"] if e["t"] == "route")
    assert not any(e["t"] == "notice" for e in after["events"]), "the notice belongs to the run that crossed L3"
    fresh = r["fresh_public"]["value"]
    # the recording's own level again (below L3: it read internal L2 pages), on the external route
    assert fresh["route"] == "external" and fresh["hwm"] <= 2, "a new thread is the only reset"


def test_local_down_sees_the_final_level(r):
    for name in ("eng_public_raised_local_down", "eng_q_raised_local_down"):
        run = r[name]["value"]
        assert run["status"] == "error" and run["error"]["code"] == "local_unavailable", name
        assert not run["output"]["text"], name


def test_approver_does_not_see_the_requesters_resumed_thread(r):
    run = r["approve_ok"]["value"]["run"]
    if run is not None:
        assert run["output"] == {"text": "", "citations": [], "data": {}}
        assert {e["t"] for e in run["events"]} <= {"route", "approval", "hwm", "notice"}


def test_approval_inbox_lists_pending_only(r):
    assert all(p["status"] == "pending" for p in r["head_to_approve_after"]["value"])
    decided = r["approve_ok"]["value"]["proposal"]["proposal_id"]
    assert decided not in {p["proposal_id"] for p in r["head_to_approve_after"]["value"]}


def test_trace_links_only_where_the_trace_answers(r):
    assert r["has_trace_own"] is True
    assert r["has_trace_other"] is False
    assert r["has_trace_unrecorded"] is False


def test_app_samples_prefill_only_when_runnable(r):
    assert r["sample_sales_staff"]["runnable"] is True
    assert r["sample_sales_head"]["runnable"] is True  # the head of sales has own recordings (WR-07)
    other = r["sample_no_recording"]
    assert other is None or other["runnable"] is False


# ---- milestone extension points (KB.demo.register, fixtures/<name>.js) -------------------------------

def test_every_milestone_registers_its_demo_extension(r):
    assert r["registered"] == ["hr", "legal", "report", "sales"]
    assert r["ext_dup"] == "refused", "a name is registered once; a second file cannot take it over"


def test_extension_routes_need_a_persona_and_never_shadow_shared_routes(r):
    _err(r, "ext_route_no_persona", 401, "unauthenticated")
    assert r["ext_route"]["value"] == {"persona": "legal_staff", "me": "u0031"}
    assert r["ext_cannot_shadow"]["value"]["user_id"] == "u0031", "GET /api/me stays the shared handler"


def test_extension_runs_obey_the_shared_rules(r):
    plain = r["ext_run_plain"]["value"]  # the public recording: external, below L3 (it read internal L2 pages)
    assert plain["route"] == "external" and plain["hwm"] <= 2
    raised = r["ext_run_raised"]["value"]  # the user may raise the level
    assert raised["route"] == "local" and raised["hwm"] == 3
    thread = r["ext_run_thread"]["value"]  # the thread keeps it
    assert thread["route"] == "local" and thread["hwm"] == 3
    assert r["ext_route_run"]["value"]["hwm"] == 3, "a run returned by an extension route is processed too"
    ext_down = r["ext_run_external_down"]["value"]
    assert ext_down["route"] == "local" and any(e.get("reason") == "external_down_fallback" for e in ext_down["events"])
    local_down = r["ext_run_local_down"]["value"]
    assert local_down["status"] == "error" and local_down["error"]["code"] == "local_unavailable"
    assert not local_down["output"]["text"]


def test_extensions_cannot_widen_access(r):
    _err(r, "ext_wrong_app", 404, "demo_not_recorded")  # a recording for another app is refused
    _err(r, "ext_run_forbidden", 403, "forbidden")
    _err(r, "ext_route_run_forbidden", 403, "forbidden")


def test_milestone_fixture_files_are_merged(r):
    assert r["ext_inits_after_reload"] is True
    run = r["fixture_run"]["value"]
    assert run["run_id"] == "run_w0fix" and run["app"] == "legal_review"
    assert r["fixture_trace_own"] is True and r["fixture_trace_other"] is False
