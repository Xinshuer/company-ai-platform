"""decide() and RLS agree on hr.screenings rows too (issues-log D-08).

The seed has no screening decisions (tests/security/test_permission_matrix_rls.py checks that and
skips the table), so this module writes real ones through corpai.hr.record_decision, the only write
path, and then compares, for every persona, service account and hiring manager, the policy decision
on each row with what RLS shows. tests/hr/conftest.py removes the rows when the module ends.
"""

from __future__ import annotations

import pytest

from corpai.policy import decide
from corpai.synth.personas import PERSONAS, SERVICE_ACCOUNTS
from corpai.types import Action
from tests.integration.rows import resource_of, visible_rows
from tests.hr.conftest import owner_rows

pytestmark = pytest.mark.anyio

READ_ACTIONS = (Action.READ, Action.SEARCH, Action.EXPORT)


async def test_decide_equals_rls_for_screening_rows(personas, planted_pairs, candidate_of, principal_of):
    from corpai.hr import record_decision

    # HR staff on their own requisition, and the head of HR on two others (one with a hiring manager)
    await record_decision(personas["hr_staff"], candidate_id=planted_pairs[1]["candidates"][0], decision="hold",
                          reason="Check the notice period in the first interview.")
    for req in ("R-03", "R-06"):
        await record_decision(personas["hr_head"], candidate_id=candidate_of(req), decision="advance",
                              reason="Meets the stated requirements; invite to a first interview.")
    rows = owner_rows("SELECT * FROM hr.screenings ORDER BY screening_id")
    assert len({r["req_id"] for r in rows}) >= 2

    hiring_managers = {r["hiring_manager_id"] for r in owner_rows(
        "SELECT DISTINCT hiring_manager_id FROM hr.requisitions WHERE req_id = ANY(%s)",
        ([r["req_id"] for r in rows],)) if r["hiring_manager_id"]}
    principals = {k: personas[k] for k in PERSONAS} | {s: principal_of(s) for s in SERVICE_ACCOUNTS} \
        | {u: principal_of(u) for u in hiring_managers}

    mismatches, seen_by = [], set()
    for key, p in principals.items():
        visible = {r["screening_id"] for r in visible_rows(p, ["hr.screenings"])["hr.screenings"]}
        for row in rows:
            rls = row["screening_id"] in visible
            seen_by.update({key} if rls else set())
            res = resource_of("hr.screenings", row, (row["screening_id"],))
            for action in READ_ACTIONS:
                d = decide(p, action, res)
                if d.allow is not rls:
                    mismatches.append((key, row["req_id"], action.value, f"rls={rls}", d.rule_id))
    assert mismatches == []
    assert {"hr_head", "hr_staff"} <= seen_by  # the check is not vacuous
