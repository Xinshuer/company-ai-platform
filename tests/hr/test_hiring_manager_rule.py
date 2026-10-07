"""Hiring managers see their own requisitions' candidates (design-review 五 简历、候选人; issues-log D-05).

The tool rule `tool-hris-hiring-manager` admits any active human who holds req:<id> to
hris.list_candidates / hris.get_candidate; RLS on hr.candidates then limits rows to those
requisitions. No agent in config/agents.yaml lets a non-HR hiring manager start a candidate view
yet (core change request), so the end-to-end checks below install that agent in a patched loader.
The table cases live in tests/security/permission_matrix.yaml (`hiring_manager_tools`).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from corpai.db import pg
from corpai.policy import agents as agents_mod
from corpai.policy import rules, tool_allowed
from corpai.policy.model import AgentSpec
from corpai.types import Role
from tests.policy.fake_personas import principal as fake_principal

MATRIX = yaml.safe_load((Path(__file__).parents[1] / "security" / "permission_matrix.yaml").read_text(encoding="utf-8"))
CASES = MATRIX["hiring_manager_tools"]

# The agent requested from the integrator (config/agents.yaml); mirrored here for the checks.
HR_CANDIDATES = AgentSpec(
    name="hr_candidates", description="Hiring manager's read-only view of their requisitions' candidates.",
    apps=("hr_candidates",), start_roles=frozenset({Role.EMPLOYEE, Role.MANAGER, Role.CEO}), start_depts=frozenset(),
    tools=frozenset({"hris.list_requisitions", "hris.list_candidates", "hris.get_candidate"}), purpose=None)


@pytest.fixture
def with_candidates_agent(monkeypatch):
    real = agents_mod.load_agents

    def patched():
        return {**real(), "hr_candidates": HR_CANDIDATES}

    monkeypatch.setattr(agents_mod, "load_agents", patched)


def _rule() -> rules.ToolRule:
    (r,) = [r for r in rules.TOOL_RULES if r.id == "tool-hris-hiring-manager"]
    return r


def test_rule_covers_only_the_two_read_tools():
    r = _rule()
    assert r.domain == "hris" and r.tools == frozenset({"list_candidates", "get_candidate"})


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['persona']}-{c['tool']}")
def test_matrix_cases(with_candidates_agent, case):
    p = fake_principal(case["persona"])
    domain, _, tool = case["tool"].partition(".")
    d = tool_allowed(p, "hr_candidates", domain, tool)
    assert d.allow is (case["expect"] == "allow"), d
    if "rule" in case:
        assert d.rule_id == case["rule"], d


def test_ops_roles_and_inactive_users_are_never_hiring_managers(with_candidates_agent):
    admin = fake_principal("platform_admin")
    forged = replace(admin, groups=admin.groups | {"req:R-01"})
    assert not _rule().who(forged, "hr_candidates")
    hm = fake_principal("eng_head")
    assert not tool_allowed(replace(hm, active=False), "hr_candidates", "hris", "list_candidates").allow


def test_existing_agents_unchanged(principal_of):
    # Without the new agent a non-HR hiring manager still cannot start hr_screening (D6: HR screens).
    hm = principal_of("u0112")
    d = tool_allowed(hm, "hr_screening", "hris", "list_candidates")
    assert not d.allow and d.rule_id == "agent-start-denied"


def test_seeded_hiring_manager_end_to_end(with_candidates_agent, principal_of):
    hm = principal_of("u0112")  # Engineering Lead, hiring manager of R-01 (role employee)
    assert hm.role is Role.EMPLOYEE and "req:R-01" in hm.groups
    assert tool_allowed(hm, "hr_candidates", "hris", "list_candidates").rule_id == "tool-hris-hiring-manager"
    assert not tool_allowed(hm, "hr_candidates", "hris", "list_requisitions").allow  # not a head, not HR
    with pg.principal_tx_sync(hm) as conn:
        reqs = {r["req_id"] for r in conn.execute("SELECT DISTINCT req_id FROM hr.candidates").fetchall()}
        evidence = conn.execute("SELECT count(*) AS n FROM hr.screening_evidence").fetchone()["n"]
    assert reqs == {r.removeprefix("req:") for r in hm.groups if r.startswith("req:")}
    assert evidence == 0  # screening results are L4: the hiring manager (L3) sees candidates, not evaluations
