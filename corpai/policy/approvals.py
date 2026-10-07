"""can_approve (design-review 二.12, 五): who may approve a write proposal.

tier 2 (internal, reversible): the requester confirms, or the head of the domain's department, or
the CEO. tier 3 (outbound / irreversible): four-eyes, approver != requester, and the approver is
the head of the domain's department or the CEO.
"""

from __future__ import annotations

from corpai.policy.catalogue import DOMAIN_DEPT, tool_tier
from corpai.policy.model import ProposalView
from corpai.types import Decision, Principal, Role, ToolTier


def _deny(rule_id: str, reason: str) -> Decision:
    return Decision(allow=False, rule_id=rule_id, reason=reason)


def _allow(rule_id: str, reason: str) -> Decision:
    return Decision(allow=True, rule_id=rule_id, reason=reason)


def can_approve(p: Principal, proposal: ProposalView) -> Decision:
    if not p.active:
        return _deny("deny-inactive", "inactive users cannot approve anything")
    if p.role is Role.SERVICE:
        return _deny("approve-no-service", "service accounts never approve proposals")
    dept = DOMAIN_DEPT.get(proposal.domain)
    if dept is None:
        return _deny("approve-unknown-domain", f"domain {proposal.domain!r} has no write tools to approve")
    catalogue_tier = tool_tier(proposal.domain, proposal.tool)
    if catalogue_tier is None or catalogue_tier is ToolTier.READ:
        return _deny("approve-unknown-tool", f"{proposal.domain}.{proposal.tool} is not a write tool")
    # The catalogue decides the tier; a proposal claiming a lower tier must not dodge four-eyes.
    try:
        claimed = ToolTier(int(proposal.tier))
    except (TypeError, ValueError):
        return _deny("approve-bad-tier", "the proposal's tier is not valid")
    tier = max(claimed, catalogue_tier)

    is_requester = p.user_id == proposal.requester_id
    is_domain_head = p.role is Role.MANAGER and p.dept == dept
    is_ceo = p.role is Role.CEO

    if tier is ToolTier.INTERNAL:
        if is_requester:
            return _allow("approve-t2-requester", "tier 2 (internal, reversible): the requester may confirm")
        if is_domain_head:
            return _allow("approve-t2-head", f"tier 2: head of {dept} approves {proposal.domain} proposals")
        if is_ceo:
            return _allow("approve-t2-ceo", "tier 2: the CEO may approve")
        return _deny("approve-t2-not-authorised",
                     f"tier 2 needs the requester, the head of {dept} or the CEO")

    if is_requester:
        return _deny("approve-t3-four-eyes", "tier 3 needs a second person: you cannot approve your own request")
    if is_domain_head:
        return _allow("approve-t3-head", f"tier 3 four-eyes: head of {dept} approves")
    if is_ceo:
        return _allow("approve-t3-ceo", "tier 3 four-eyes: the CEO approves")
    return _deny("approve-t3-not-authorised", f"tier 3 needs the head of {dept} or the CEO")
