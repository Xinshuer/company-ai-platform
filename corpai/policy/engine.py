"""The policy entry points (architecture §6). Pure functions over the tables in rules.py.

Evaluation order in decide(): deny rules (any match wins) → unknown kind → derived actions
(EXPORT = READ + watermark) → first matching allow rule → default deny.
"""

from __future__ import annotations

from corpai.policy import rules
from corpai.policy.agents import agent_spec, may_start
from corpai.policy.approvals import can_approve
from corpai.policy.catalogue import TOOL_CATALOGUE
from corpai.policy.model import ProposalView, RagFilter
from corpai.types import Action, Decision, Level, Principal, Resource, Role

DEFAULT_DENY = "default-deny"


def _deny_reason(p: Principal, action: Action, r: Resource) -> str:
    """A specific reason for the default deny, so users and auditors see *why*."""
    if r.kind in rules.LABELLED_KINDS and action in (Action.READ, Action.SEARCH, Action.PROPOSE):
        if int(r.label.level) > int(p.clearance):
            return (f"{r.kind} is L{int(r.label.level)}, above your clearance "
                    f"L{int(p.clearance)}")
        return f"you are not in any ACL group of this {r.kind}"
    return f"no rule allows {action} on {r.kind} for {p.role} in {p.dept}"


def decide(p: Principal, action: Action, r: Resource) -> Decision:
    action = Action(action)
    for rule in rules.DENY_RULES:
        if rule.matches(p, action, r):
            return Decision(False, rule.id, rule.reason)
    if r.kind not in rules.ALL_KINDS:
        return Decision(False, DEFAULT_DENY, f"unknown resource kind {r.kind!r}")
    for d in rules.DERIVED:
        if d.action is action:
            base = decide(p, d.from_action, r)
            if not base.allow:
                return Decision(False, base.rule_id, f"{action} needs {d.from_action}: {base.reason}")
            return Decision(True, d.rule_id, f"{action} allowed because {d.from_action} is ({base.rule_id})",
                            field_mask=base.field_mask, obligations=base.obligations | d.obligations)
    for rule in rules.ALLOW_RULES:
        if rule.matches(p, action, r):
            return Decision(True, rule.id, rule.reason, field_mask=rule.field_mask,
                            obligations=rule.obligations)
    return Decision(False, DEFAULT_DENY, _deny_reason(p, action, r))


def rag_filter(p: Principal, *, purpose: str) -> RagFilter:
    """The retrieval filter the backend pushes down to pgvector (RLS still applies underneath)."""
    if purpose not in rules.PURPOSES:
        raise ValueError(f"unknown retrieval purpose {purpose!r}")
    collections_for, min_trust = rules.PURPOSES[purpose]
    if not p.active or p.role is Role.SERVICE:
        # Nothing to search: an empty collection set matches no rows.
        return RagFilter(collections=frozenset(), max_level=Level.L1, groups=frozenset(),
                         min_trust=min_trust, current_only=True)
    collections = frozenset(collections_for(p))
    if p.role in rules.OPS_ROLES:
        collections &= rules.OPS_ROLE_COLLECTIONS
    return RagFilter(collections=collections, max_level=p.clearance, groups=p.groups,
                     min_trust=min_trust, current_only=True)


def tool_allowed(p: Principal, agent: str, domain: str, tool: str) -> Decision:
    """User rule ∩ agent allowlist (config/agents.yaml). The token scope is checked by the server."""
    if not p.active:
        return Decision(False, "deny-inactive", "the account is deactivated")
    if tool not in TOOL_CATALOGUE.get(domain, {}):
        return Decision(False, "tool-unknown", f"{domain}.{tool} is not in the tool catalogue")
    spec = agent_spec(agent)
    if spec is None:
        return Decision(False, "agent-unknown", f"no agent named {agent!r}")
    if f"{domain}.{tool}" not in spec.tools:
        return Decision(False, "agent-allowlist", f"agent {agent} does not hold {domain}.{tool}")
    started = may_start(p, agent)
    if not started.allow:
        return Decision(False, started.rule_id, started.reason)
    for tr in rules.TOOL_RULES:
        if tr.domain == domain and (tr.tools is None or tool in tr.tools) and tr.who(p, agent):
            return Decision(True, tr.id, f"{tr.reason}; {agent} holds {domain}.{tool}")
    return Decision(False, DEFAULT_DENY, f"{p.role} in {p.dept} may not use {domain}.{tool}")


__all__ = ["decide", "rag_filter", "tool_allowed", "can_approve", "ProposalView", "RagFilter"]
