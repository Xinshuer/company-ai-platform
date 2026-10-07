"""Table-driven policy cases (tests/policy/cases.yaml) over the 14 personas."""

from __future__ import annotations

import pytest

from corpai.policy import ProposalView, can_approve, decide, rag_filter, tool_allowed
from corpai.types import Action, Level, ToolTier, Trust
from tests.policy.casedata import CASES, RESOURCES
from tests.policy.fake_personas import PERSONA_KEYS, principal


def _expand(entries: list[dict], key_fn) -> list:
    """Closed world over the 14 personas, plus the entry's `extra` variant cases."""
    out = []
    for e in entries:
        allowed = PERSONA_KEYS if e["allow"] == "*" else e["allow"]
        unknown = set(allowed) - set(PERSONA_KEYS)
        assert not unknown, f"unknown personas in {e}: {unknown}"
        obligations = e.get("obligations") or {}
        for who in PERSONA_KEYS:
            exp_allow = who in allowed
            exp_obl = frozenset(obligations.get(who, [])) if exp_allow else frozenset()
            out.append(pytest.param(e, who, exp_allow, exp_obl, None, id=f"{key_fn(e)}-{who}"))
        for who, verdict, *rule in e.get("extra") or []:
            out.append(pytest.param(e, who, verdict == "allow", None, rule[0] if rule else None,
                                    id=f"{key_fn(e)}-{who}"))
    return out


DECIDE = _expand(CASES["decide"], lambda e: f"{e['resource']}.{e['action']}")
TOOLS = _expand(CASES["tools"], lambda e: f"{e['agent']}.{e['tool']}")
APPROVE = _expand(CASES["approve"], lambda e: f"{e['domain']}.{e['tool']}.t{e['tier']}.by-{e['requester']}")


def test_case_table_is_big_enough():
    assert len(DECIDE) >= 120
    assert len(DECIDE) + len(TOOLS) + len(APPROVE) >= 500


def _check(d, exp_allow: bool, exp_obl, exp_rule) -> None:
    assert d.allow is exp_allow, d
    assert d.rule_id and d.reason  # every decision explains itself
    if not exp_allow:
        assert d.obligations == frozenset() and d.field_mask == frozenset()
    if exp_obl is not None:
        assert d.obligations == exp_obl, d
    if exp_rule is not None:
        assert d.rule_id == exp_rule, d


@pytest.mark.parametrize("entry,who,exp_allow,exp_obl,exp_rule", DECIDE)
def test_decide(entry, who, exp_allow, exp_obl, exp_rule):
    d = decide(principal(who), Action(entry["action"]), RESOURCES[entry["resource"]])
    _check(d, exp_allow, exp_obl, exp_rule)


@pytest.mark.parametrize("entry,who,exp_allow,exp_obl,exp_rule", TOOLS)
def test_tool_allowed(entry, who, exp_allow, exp_obl, exp_rule):
    domain, _, tool = entry["tool"].partition(".")
    d = tool_allowed(principal(who), entry["agent"], domain, tool)
    _check(d, exp_allow, exp_obl, exp_rule)


@pytest.mark.parametrize("entry,who,exp_allow,exp_obl,exp_rule", APPROVE)
def test_can_approve(entry, who, exp_allow, exp_obl, exp_rule):
    view = ProposalView(requester_id=principal(entry["requester"]).user_id, domain=entry["domain"],
                        tool=entry["tool"], tier=ToolTier(entry["tier"]))
    _check(can_approve(principal(who), view), exp_allow, exp_obl, exp_rule)


@pytest.mark.parametrize("entry", CASES["rag"], ids=lambda e: f"{e['persona']}.{e['purpose']}")
def test_rag_filter(entry):
    p = principal(entry["persona"])
    f = rag_filter(p, purpose=entry["purpose"])
    assert f.collections == frozenset(entry["collections"])
    assert f.max_level == Level(entry["max_level"])
    assert f.min_trust == (Trust(entry["min_trust"]) if entry["min_trust"] else None)
    assert f.current_only is True
    assert f.groups == (frozenset(entry["groups"]) if "groups" in entry else p.groups)
