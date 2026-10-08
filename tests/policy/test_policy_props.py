"""Structural properties of the policy tables and agents.yaml, beyond the case table."""

from __future__ import annotations

import random

import pytest

from corpai import policy
from corpai.policy import agents as agents_mod
from corpai.policy import rules
from corpai.policy.catalogue import COLLECTIONS, TOOL_CATALOGUE
from corpai.types import Action, Label, Level, Resource, Role, ToolTier
from tests.policy.casedata import GROUP_POOL
from tests.policy.fake_personas import PERSONA_KEYS, VARIANTS, all_personas, persona, principal


def _can_read(p, label: Label) -> bool:
    """core.can_read() from migration 000, transcribed: what RLS will do with this principal."""
    if not p.active:
        return False  # pg._session_vars sends no groups and clearance 0 for inactive users
    return int(label.level) <= int(p.clearance) and bool(label.acl_groups & p.groups)


def test_label_reads_agree_with_rls_formula():
    """decide(READ|SEARCH) on any labelled row == core.can_read, except the explicit ops-role deny."""
    rng = random.Random(7)
    people = list(all_personas().values()) + list(VARIANTS.values())
    kinds = sorted(policy.LABELLED_KINDS)
    checked = 0
    for _ in range(400):
        label = Label(level=Level(rng.randint(1, 4)),
                      acl_groups=frozenset(rng.sample(GROUP_POOL, rng.randint(0, 4))))
        r = Resource(kind=rng.choice(kinds), id="x", label=label)
        for p in people:
            for action in (Action.READ, Action.SEARCH):
                d = policy.decide(p, action, r)
                expected = _can_read(p, label)
                if p.role in rules.OPS_ROLES and label.level >= Level.L3:
                    expected = False
                assert d.allow is expected, (p.user_id, r, d)
                checked += 1
    assert checked > 10_000


def test_policy_never_allows_more_than_rls():
    """Wherever decide() allows a labelled read, RLS must too (the converse may be narrowed)."""
    rng = random.Random(11)
    for _ in range(300):
        label = Label(level=Level(rng.randint(1, 4)),
                      acl_groups=frozenset(rng.sample(GROUP_POOL, rng.randint(0, 5))))
        r = Resource(kind="doc", id="x", label=label)
        for p in list(all_personas().values()) + list(VARIANTS.values()):
            if policy.decide(p, Action.READ, r).allow:
                assert _can_read(p, label)


@pytest.mark.parametrize("kind", sorted(rules.ALL_KINDS))
@pytest.mark.parametrize("action", list(Action))
def test_inactive_denied_everything(kind, action):
    p = principal("ceo~inactive")
    r = Resource(kind=kind, id="x", label=Label(level=Level.L1, acl_groups=frozenset({"all"})),
                 attrs={"owner_id": p.user_id, "requester_id": p.user_id, "domain": "crm",
                        "tool": "log_interaction", "tier": 2, "dept": "exec"})
    d = policy.decide(p, action, r)
    assert not d.allow and d.rule_id == "deny-inactive"


def test_inactive_has_no_groups_tools_or_apps():
    p = principal("sales_staff~inactive")
    assert p.groups == frozenset()
    assert policy.apps_for(p) == []
    for agent, spec in policy.load_agents().items():
        for t in spec.tools:
            d, _, tool = t.partition(".")
            assert not policy.tool_allowed(p, agent, d, tool).allow


def test_rule_ids_unique_and_explained():
    ids = [r.id for r in rules.DENY_RULES + rules.ALLOW_RULES] + [d.rule_id for d in rules.DERIVED] \
        + [t.id for t in rules.TOOL_RULES]
    assert len(ids) == len(set(ids))
    for r in rules.DENY_RULES + rules.ALLOW_RULES:
        assert r.reason and r.kinds and r.actions
    for t in rules.TOOL_RULES:
        assert t.domain in TOOL_CATALOGUE
        assert t.tools is None or t.tools <= set(TOOL_CATALOGUE[t.domain])


def test_unknown_purpose_and_action_fail_loudly():
    with pytest.raises(ValueError):
        policy.rag_filter(persona("ceo"), purpose="everything")
    with pytest.raises(ValueError):
        policy.decide(persona("ceo"), "delete_all", Resource(kind="doc", id="x", label=Label(level=Level.L1)))


def test_string_actions_accepted():
    r = Resource(kind="doc", id="x", label=Label(level=Level.L1, acl_groups=frozenset({"all"})))
    assert policy.decide(persona("sales_staff"), "read", r).allow


def test_rag_filter_collections_are_known_and_capped_by_clearance():
    for key in PERSONA_KEYS:
        p = persona(key)
        for purpose in rules.PURPOSES:
            f = policy.rag_filter(p, purpose=purpose)
            assert f.collections <= COLLECTIONS
            assert f.max_level == p.clearance
            assert f.groups == p.groups
            if p.role in rules.OPS_ROLES:
                assert f.collections <= {"public", "general"}


def test_proposal_view_from_bad_attrs():
    assert policy.ProposalView.from_attrs({}) is None
    assert policy.ProposalView.from_attrs({"requester_id": "u1", "domain": "crm", "tool": "x", "tier": 9}) is None
    v = policy.ProposalView.from_attrs({"requester_id": "u1", "domain": "crm", "tool": "send_email", "tier": "3"})
    assert v is not None and v.tier is ToolTier.EXTERNAL


# ---- agents.yaml -------------------------------------------------------------------------------

REQUIRED_AGENTS = {"qa", "legal_review", "report", "sales_profile", "sales_web", "hr_headcount", "hr_screening",
                   "hr_candidates"}
WRITE_TOOLS = {f"{d}.{t}" for d, tools in TOOL_CATALOGUE.items() for t, tier in tools.items() if tier > ToolTier.READ}
WEB_TOOLS = {"web.search", "web.fetch"}


def test_agents_yaml_shape():
    specs = policy.load_agents()
    assert REQUIRED_AGENTS <= set(specs)
    for name, s in specs.items():
        assert s.description and s.tools
        assert s.start_roles or s.start_depts
        assert Role.SERVICE not in s.start_roles
        if s.purpose is not None:
            assert s.purpose in rules.PURPOSES


def test_web_is_isolated_in_sales_web():
    specs = policy.load_agents()
    assert specs["sales_web"].tools == WEB_TOOLS  # untrusted input: nothing else
    assert specs["sales_web"].apps == () and specs["sales_web"].subagent_of == "sales_profile"
    for name, s in specs.items():
        if name != "sales_web":
            assert not (s.tools & WEB_TOOLS), name


def test_rule_of_two_in_allowlists():
    specs = policy.load_agents()
    # Untrusted-input agents hold no write or outbound tools (design-review 二.6): web pages and
    # résumés (hr_candidates serves résumé text through hris.get_candidate).
    for name in ("sales_web", "hr_screening", "hr_candidates"):
        assert not (specs[name].tools & WRITE_TOOLS), name
    # The report agent aggregates everything: no writes, no web.
    assert not (specs["report"].tools & (WRITE_TOOLS | WEB_TOOLS))
    assert not (specs["qa"].tools & WRITE_TOOLS)


def test_bad_agent_config_rejected():
    with pytest.raises(agents_mod.AgentConfigError):
        agents_mod._spec("x", {"tools": ["crm.drop_everything"], "start": {"roles": ["ceo"]}})
    with pytest.raises(agents_mod.AgentConfigError):
        agents_mod._spec("x", {"tools": ["docs.get_doc"], "start": {"roles": ["service"]}})
    with pytest.raises(agents_mod.AgentConfigError):
        agents_mod._spec("x", {"tools": ["docs.get_doc"], "start": {"roles": ["wizard"]}})


# hr_candidates (D-14) is open to every employee, head and the CEO: its tools and RLS limit it to
# the caller's own requisitions, so opening it shows the rest an explicit "no requisitions" result.
@pytest.mark.parametrize("key,apps", [
    ("sales_staff", ["qa", "sales_profile", "hr_candidates"]),
    ("legal_staff", ["qa", "legal_review", "hr_candidates"]),
    ("hr_staff", ["qa", "hr_headcount", "hr_screening", "hr_candidates"]),
    ("eng_head", ["qa", "report", "hr_headcount", "hr_candidates"]),
    ("ceo", ["qa", "legal_review", "report", "sales_profile", "hr_headcount", "hr_candidates"]),
    ("platform_admin", ["qa"]),
    ("auditor", ["qa"]),
    ("svc_metrics", []),
])
def test_apps_for(key, apps):
    assert sorted(policy.apps_for(principal(key))) == sorted(apps)


def test_every_write_tool_has_an_owner_and_an_approver():
    """Each write tool is usable by someone through some agent, and someone else can approve it."""
    people = all_personas()
    specs = policy.load_agents()
    for wt in WRITE_TOOLS:
        domain, _, tool = wt.partition(".")
        users = {k for k, p in people.items() for a in specs if policy.tool_allowed(p, a, domain, tool).allow}
        if not users:
            continue  # held by no agent (UNHELD_WRITE_TOOLS below)
        for k in users:
            view = policy.ProposalView(people[k].user_id, domain, tool, policy.tool_tier(domain, tool))
            approvers = {a for a, p in people.items() if a != k and policy.can_approve(p, view).allow}
            assert approvers, (wt, k)


# Write tools with an MCP server, an executor and tool rules that no agent holds, so no token can call them
# (issues-log D-06/D-16): kept for a later engineering or finance agent, tested through a test-only agent
# (tests/mcp/support.py). Giving one to an agent, or orphaning another, is a decision: it shows up here.
UNHELD_WRITE_TOOLS = {"code.create_ticket", "code.comment_ticket", "finance.post_journal_entry"}


def test_unheld_write_tools_are_exactly_the_known_ones():
    held = {t for spec in policy.load_agents().values() for t in spec.tools}
    assert WRITE_TOOLS - held == UNHELD_WRITE_TOOLS


def test_can_approve_rejects_malformed_tier():
    view = policy.ProposalView(requester_id="u0004", domain="crm", tool="send_email", tier="high")
    d = policy.can_approve(persona("ceo"), view)
    assert not d.allow and d.rule_id == "approve-bad-tier"
