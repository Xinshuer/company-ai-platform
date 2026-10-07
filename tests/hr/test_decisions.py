"""The human screening decision (GDPR Art. 22; D6): HR on the requisition or the head of HR, reason required.

The checks run twice: in Python (fast, friendly message) and in the SECURITY DEFINER function, so a
caller that skips the Python layer is refused the same way. Every attempt is audited.
"""

from __future__ import annotations

import psycopg
import pytest

from corpai.db import pg
from corpai.hr import DECISIONS, NoPermission, record_decision, screen_candidate
from corpai.policy import TOOL_CATALOGUE, load_agents
from tests.hr.conftest import owner_rows

pytestmark = pytest.mark.anyio


def _screenings(cid: str) -> list[dict]:
    return owner_rows("SELECT * FROM hr.screenings WHERE candidate_id = %s ORDER BY decided_at", (cid,))


def _decision_audits(user_id: str, cid: str) -> list[dict]:
    return owner_rows("SELECT * FROM audit.log WHERE actor = %s AND action = 'hr.record_decision' AND resource = %s "
                      "ORDER BY seq", (user_id, f"hr.candidate:{cid}"))


async def test_hr_staff_records_a_decision_with_the_evidence_seen(personas, planted_pairs, run_id):
    cid = planted_pairs[1]["candidates"][0]
    ev = await screen_candidate(personas["hr_staff"], candidate_id=cid, run_id=run_id)
    sid = await record_decision(personas["hr_staff"], candidate_id=cid, decision="hold",
                                reason="  Strong routing background; check notice period in the interview.  ")
    (row,) = [r for r in _screenings(cid) if r["screening_id"] == sid]
    assert row["decision"] == "hold" and row["decided_by"] == personas["hr_staff"].user_id
    assert row["reason"] == "Strong routing background; check notice period in the interview."
    assert row["decided_at"] is not None and row["level"] == 4
    assert sorted(row["acl_groups"]) == sorted([f"req:{ev.req_id}", "head:hr"])
    assert [i["requirement_id"] for i in row["evidence"]] == [i.requirement_id for i in ev.items]
    (audit_row,) = [a for a in _decision_audits(personas["hr_staff"].user_id, cid) if a["decision"] == "allow"]
    assert audit_row["details"]["decision"] == "hold"
    assert "notice period" not in str(audit_row)  # the reason stays in the L4 table, not the audit log


async def test_head_of_hr_may_decide_on_any_requisition(personas, candidate_of):
    cid = candidate_of("R-06")
    sid = await record_decision(personas["hr_head"], candidate_id=cid, decision="advance",
                                reason="Meets the stated requirements; invite to a first interview.")
    assert any(r["screening_id"] == sid for r in _screenings(cid))


@pytest.mark.parametrize("reason", ["", "   ", "\n\t", None, "x" * 2001])
async def test_reason_is_required(personas, planted_pairs, reason):
    cid = planted_pairs[1]["candidates"][1]
    with pytest.raises(ValueError, match="reason"):
        await record_decision(personas["hr_staff"], candidate_id=cid, decision="reject", reason=reason)
    assert _screenings(cid) == []
    assert _decision_audits(personas["hr_staff"].user_id, cid)[-1]["decision"] == "deny"


@pytest.mark.parametrize("decision", ["auto_reject", "", "REJECT", "score:12"])
async def test_only_three_decisions_exist(personas, planted_pairs, decision):
    with pytest.raises(ValueError):
        await record_decision(personas["hr_staff"], candidate_id=planted_pairs[1]["candidates"][1],
                              decision=decision, reason="because")
    assert DECISIONS == ("advance", "reject", "hold")


@pytest.mark.parametrize("who", ["sales_head", "eng_head", "ceo", "finance_head", "legal_staff", "platform_admin",
                                 "auditor"])
async def test_non_hr_cannot_decide(personas, planted_pairs, who):
    cid = planted_pairs[0]["candidates"][1]
    with pytest.raises(NoPermission):
        await record_decision(personas[who], candidate_id=cid, decision="reject", reason="not a fit")
    assert _screenings(cid) == []
    assert _decision_audits(personas[who].user_id, cid)[-1]["decision"] == "deny"


async def test_hiring_manager_cannot_decide(principal_of, planted_pairs):
    cid = planted_pairs[0]["candidates"][1]
    with pytest.raises(NoPermission):
        await record_decision(principal_of("u0112"), candidate_id=cid, decision="advance", reason="looks good")
    assert _screenings(cid) == []


async def test_hr_staff_not_on_the_requisition_cannot_decide(personas, candidate_of):
    cid = candidate_of("R-05")  # recruited by another HR colleague
    with pytest.raises(NoPermission, match="no permission"):
        await record_decision(personas["hr_staff"], candidate_id=cid, decision="reject", reason="not a fit")
    with pytest.raises(NoPermission, match="no permission"):
        await record_decision(personas["hr_staff"], candidate_id="CAND-999", decision="reject", reason="x")


async def test_sql_layer_refuses_without_python_checks(personas, planted_pairs):
    cid = planted_pairs[2]["candidates"][0]
    async with pg.principal_tx(personas["hr_staff"]) as conn:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            await conn.execute("SELECT hr.record_screening_decision('scr_aaaaaaaaaaaaaaaaaaaa', %s, 'reject', '  ')",
                               (cid,))
    async with pg.principal_tx(personas["sales_head"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT hr.record_screening_decision('scr_aaaaaaaaaaaaaaaaaaaa', %s, 'reject', 'x')",
                               (cid,))
    async with pg.principal_tx(personas["hr_head"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # no direct table write for the app role
            await conn.execute("INSERT INTO hr.screenings (screening_id, candidate_id, req_id, created_by, decision, "
                               "reason, decided_by, decided_at, acl_groups) VALUES ('s1', %s, 'R-03', 'u0050', "
                               "'reject', 'x', 'u0050', now(), ARRAY['all'])", (cid,))
    assert _screenings(cid) == []


async def test_inactive_principal_cannot_decide(personas, planted_pairs):
    from dataclasses import replace

    gone = replace(personas["hr_staff"], active=False)
    with pytest.raises(NoPermission):
        await record_decision(gone, candidate_id=planted_pairs[2]["candidates"][0], decision="reject", reason="x")


def test_no_model_tool_can_record_a_decision():
    """Art. 22: the decision is a person's. No MCP tool and no agent allowlist exposes it."""
    for domain, tools in TOOL_CATALOGUE.items():
        assert not [t for t in tools if "decision" in t or "screen" in t or "reject" in t], domain
    for spec in load_agents().values():
        assert not [t for t in spec.tools if "decision" in t or "reject" in t], spec.name
