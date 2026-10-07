"""Shared vocabulary. Frozen: every stream imports these; nobody redefines them (docs/architecture.md §3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any, Mapping


class Level(IntEnum):
    """Data classification. Higher = more sensitive. Routing: L1/L2 may go external, L3/L4 local only."""

    L1 = 1  # public
    L2 = 2  # internal
    L3 = 3  # confidential (any identifiable person, contracts, code, quotes)
    L4 = 4  # restricted (salary, performance, financial statements, litigation, vulnerabilities)


class Dept(StrEnum):
    SALES = "sales"
    ENG = "eng"
    LEGAL = "legal"
    FINANCE = "finance"
    HR = "hr"
    EXEC = "exec"
    IT = "it"


class Role(StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"  # head of a department (CTO, CFO, head of legal, head of HR, head of sales)
    CEO = "ceo"
    PLATFORM_ADMIN = "platform_admin"  # runs the system, sees no business content by default
    SECURITY_AUDITOR = "security_auditor"  # the only reader of the audit log
    SERVICE = "service"  # one service account per background job


class Route(StrEnum):
    LOCAL = "local"  # plays the on-prem model; today it is the DeepSeek API (decisions.md D3)
    EXTERNAL = "external"  # third-party API; L1/L2 only


class Trust(StrEnum):
    REVIEWED = "reviewed"
    INTERNAL = "internal"
    UNTRUSTED = "untrusted"  # web pages, uploaded contracts, résumés


TRUST_RANK: dict[Trust, int] = {Trust.UNTRUSTED: 0, Trust.INTERNAL: 1, Trust.REVIEWED: 2}


class Action(StrEnum):
    READ = "read"
    SEARCH = "search"
    PROPOSE = "propose"
    APPROVE = "approve"
    EXECUTE = "execute"
    EXPORT = "export"
    ADMIN = "admin"


class ToolTier(IntEnum):
    READ = 1
    INTERNAL = 2  # internal and reversible (drafts, tickets): the requester confirms
    EXTERNAL = 3  # outbound or irreversible: four-eyes approval


@dataclass(frozen=True)
class Principal:
    user_id: str  # "u0001"; service accounts "svc:<job>"
    name: str
    email: str
    dept: Dept
    role: Role
    title: str
    manager_id: str | None
    clearance: Level  # ceiling; ACL groups still decide
    groups: frozenset[str]  # effective ACL groups (architecture §5); the only thing RLS sees
    active: bool = True

    @property
    def is_head(self) -> bool:
        return self.role in (Role.MANAGER, Role.CEO)


@dataclass(frozen=True)
class Label:
    level: Level
    dept: Dept | None = None
    owner_id: str | None = None
    acl_groups: frozenset[str] = frozenset()
    trust: Trust = Trust.INTERNAL
    subject_ids: frozenset[str] = frozenset()  # GDPR data subjects
    sources: tuple[str, ...] = ()  # "kind:id", e.g. "crm.account:A-0012"


@dataclass(frozen=True)
class Resource:
    kind: str  # catalogue in architecture §6
    id: str | None
    label: Label
    attrs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    allow: bool
    rule_id: str  # the rule that decided; "default-deny" when none matched
    reason: str
    field_mask: frozenset[str] = frozenset()
    obligations: frozenset[str] = frozenset()  # e.g. {"aggregate_only", "min_group_5", "watermark"}

    def __bool__(self) -> bool:
        return self.allow
