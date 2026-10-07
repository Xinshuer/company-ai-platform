"""Value types of the policy module (architecture §6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Union

from corpai.types import Action, Level, Principal, Resource, Role, ToolTier, Trust


class Effect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


Pred = Callable[[Principal, Resource], bool]


@dataclass(frozen=True)
class Rule:
    """One row of the decision table.

    `kinds`: resource kinds the row applies to. `actions`: actions it applies to.
    `roles_or_pred`: a set of roles (matches on p.role) or a predicate (p, r) -> bool.
    """

    id: str
    kinds: frozenset[str]
    actions: frozenset[Action]
    roles_or_pred: Union[frozenset[Role], Pred]
    effect: Effect
    reason: str
    field_mask: frozenset[str] = frozenset()
    obligations: frozenset[str] = frozenset()

    def matches(self, p: Principal, action: Action, r: Resource) -> bool:
        if r.kind not in self.kinds or action not in self.actions:
            return False
        if isinstance(self.roles_or_pred, frozenset):
            return p.role in self.roles_or_pred
        return bool(self.roles_or_pred(p, r))


@dataclass(frozen=True)
class Derived:
    """`action` is allowed exactly where `from_action` is, plus extra obligations (e.g. EXPORT)."""

    action: Action
    from_action: Action
    rule_id: str
    obligations: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ToolRule:
    """Who may use which tools of one domain. `tools` None = every tool of the domain.
    `who(p, agent)`: the agent matters only for rules like `web`, usable through one agent."""

    id: str
    domain: str
    tools: frozenset[str] | None
    who: Callable[[Principal, str], bool]
    reason: str


@dataclass(frozen=True)
class RagFilter:
    collections: frozenset[str]
    max_level: Level
    groups: frozenset[str]
    min_trust: Trust | None
    current_only: bool = True


@dataclass(frozen=True)
class ProposalView:
    """What can_approve() needs to know about a proposal (stream B2 builds one from ops.proposals)."""

    requester_id: str
    domain: str
    tool: str
    tier: ToolTier

    @classmethod
    def from_attrs(cls, attrs) -> "ProposalView | None":
        try:
            return cls(requester_id=str(attrs["requester_id"]), domain=str(attrs["domain"]),
                       tool=str(attrs["tool"]), tier=ToolTier(int(attrs["tier"])))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    apps: tuple[str, ...]
    start_roles: frozenset[Role]
    start_depts: frozenset[str]
    tools: frozenset[str]  # "domain.tool"
    purpose: str | None = None  # rag_filter purpose the agent retrieves with
    subagent_of: str | None = None
    extra: dict = field(default_factory=dict)
