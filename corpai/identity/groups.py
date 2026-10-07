"""Effective ACL groups (architecture §5): the only thing RLS sees about a person.

Input is one row of `core.principal_row()` (or a dict shaped like it). The function is pure so
policy tests can build principals without a database, and it is the single place where group
names are spelled.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from corpai.types import Role

ALL = "all"


def dept_group(dept: str) -> str:
    return f"dept:{dept}"


def head_group(dept: str) -> str:
    return f"head:{dept}"


def user_group(user_id: str) -> str:
    return f"user:{user_id}"


def mgr_of_group(report_id: str) -> str:
    return f"mgr-of:{report_id}"


def role_group(role: str) -> str:
    return f"role:{role}"


# Roles that carry a role:<x> group. Plain employees and managers are described by dept/head groups.
_ROLE_GROUPS = {Role.CEO, Role.PLATFORM_ADMIN, Role.SECURITY_AUDITOR}


def _strs(values: Iterable[Any] | None) -> list[str]:
    out = []
    for v in values or ():
        if v is None:
            continue
        s = str(v)
        # A comma would split into two groups inside app.groups; the DB CHECK forbids it, and we
        # refuse here too rather than silently widening someone's access.
        if "," in s or not s:
            raise ValueError("invalid group name in directory row")
        out.append(s)
    return out


def effective_groups(row: Mapping[str, Any]) -> frozenset[str]:
    """Groups for one directory row. Inactive users get none, so RLS shows them nothing."""
    if not row.get("active", False):
        return frozenset()
    user_id = str(row["user_id"])
    role = Role(row["role"])
    dept = str(row["dept"])
    memberships = _strs(row.get("memberships"))
    grants = _strs(row.get("grants"))  # principal_row() already dropped expired and revoked grants

    if role is Role.SERVICE:
        # Service accounts ("svc:<job>") are not people: no `all`, no dept, only their job group
        # plus whatever an operator assigned explicitly.
        svc = user_id if user_id.startswith("svc:") else f"svc:{user_id}"
        return frozenset({svc, *memberships, *grants})

    groups: set[str] = {ALL, dept_group(dept), user_group(user_id), *memberships, *grants}
    if role is Role.MANAGER:
        groups.add(head_group(dept))
    if role is Role.CEO:
        groups.add(head_group("exec"))
    if role in _ROLE_GROUPS:
        groups.add(role_group(role.value))
    for report_id in _strs(row.get("direct_reports")):
        groups.add(mgr_of_group(report_id))
    return frozenset(groups)
