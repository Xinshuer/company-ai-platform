"""Fixtures for the audit tests: principals built directly (no synth needed)."""

from __future__ import annotations

from typing import Callable

import pytest

from corpai.types import Dept, Level, Principal, Role

_ROLE_GROUP = {Role.SECURITY_AUDITOR: "role:security_auditor", Role.PLATFORM_ADMIN: "role:platform_admin",
               Role.CEO: "role:ceo"}


def make_principal(user_id: str, role: Role = Role.EMPLOYEE, dept: Dept = Dept.SALES,
                   clearance: Level = Level.L3, groups: set[str] | None = None,
                   active: bool = True) -> Principal:
    if groups is None:
        groups = {"all", f"dept:{dept}", f"user:{user_id}"}
        if role in _ROLE_GROUP:
            groups.add(_ROLE_GROUP[role])
        if role == Role.MANAGER:
            groups.add(f"head:{dept}")
    return Principal(user_id=user_id, name=user_id, email=f"{user_id}@kestrelbyte.example", dept=dept,
                     role=role, title="t", manager_id=None, clearance=clearance,
                     groups=frozenset(groups), active=active)


@pytest.fixture
def mkp() -> Callable[..., Principal]:
    return make_principal


@pytest.fixture
def auditor() -> Principal:
    return make_principal("u0069", Role.SECURITY_AUDITOR, Dept.IT, Level.L2)
