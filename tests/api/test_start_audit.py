"""Every "may not start this app" refusal of the API is a 403 with a policy_decision deny row
(issues-log WR-10): POST /api/runs, the sales and HR routes (deps.require_app), like the legal pages."""

from __future__ import annotations

import pytest

from tests.api.support import owner_rows

pytestmark = pytest.mark.anyio


def _last_seq() -> int:
    return int(owner_rows("SELECT coalesce(max(seq), 0) AS s FROM audit.log")[0]["s"])


def _denials(actor: str, since: int) -> list[dict]:
    return owner_rows("SELECT action, resource, reason, act_chain FROM audit.log WHERE seq > %s AND actor = %s"
                      " AND event = 'policy_decision' AND decision = 'deny' ORDER BY seq", (since, actor))


@pytest.mark.parametrize("who,method,path,body,action,agent", [
    ("sales_staff", "POST", "/api/runs", {"app": "legal_review", "input": "review C-0004"}, "start", "legal_review"),
    ("auditor", "POST", "/api/runs", {"app": "report", "input": "2026-Q3 management report"}, "start", "report"),
    ("hr_staff", "GET", "/api/sales/accounts?q=a", None, "sales.accounts", "sales_profile"),
    ("sales_staff", "GET", "/api/hr/candidates?app=hr_screening", None, "hr.candidates", "hr_screening"),
    ("auditor", "GET", "/api/hr/candidates", None, "hr.candidates", "hr_candidates"),
    ("auditor", "GET", "/api/hr/candidates/CAND-001", None, "hr.candidate", "hr_candidates"),
])
async def test_start_refusals_are_403_and_audited(app_client, as_persona, who, method, path, body, action, agent):
    p = await as_persona(who)
    before = _last_seq()
    r = await app_client.request(method, path, json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", r.text
    rows = _denials(p.user_id, before)
    assert [(d["action"], d["resource"], d["reason"]) for d in rows] == \
        [(action, f"agent:{agent}", "agent-start-denied")], rows
    assert list(rows[0]["act_chain"]) == [f"agent:{agent}"]


async def test_allowed_starts_and_bad_app_names_write_no_denial(app_client, as_persona):
    p = await as_persona("hr_staff")
    before = _last_seq()
    assert (await app_client.get("/api/hr/candidates?app=hr_screening")).status_code == 200
    bad = await app_client.post("/api/runs", json={"app": "no_such_app", "input": "hello"})
    assert bad.status_code == 400
    assert _denials(p.user_id, before) == []  # an unknown app is a bad request, not a start refusal
