"""Resolve a Principal from the directory (architecture §5).

No cache on purpose: every request re-reads `core.principal_row()`, so a deactivated user or a
revoked / expired grant loses access on the very next call.
"""

from __future__ import annotations

from typing import Any, Mapping

from corpai.config import settings
from corpai.identity import _db
from corpai.identity.groups import effective_groups
from corpai.types import Dept, Level, Principal, Role

_PRINCIPAL_SQL = "SELECT * FROM core.principal_row(%s)"
_PERSONAS_SQL = "SELECT * FROM core.list_personas()"


def principal_from_row(row: Mapping[str, Any]) -> Principal:
    """Build a Principal from a principal_row() row. Raises ValueError on malformed rows."""
    return Principal(
        user_id=str(row["user_id"]),
        name=str(row["name"]),
        email=str(row["email"]),
        dept=Dept(row["dept"]),
        role=Role(row["role"]),
        title=str(row["title"]),
        manager_id=row.get("manager_id"),
        clearance=Level(int(row["clearance"])),
        groups=effective_groups(row),
        active=bool(row["active"]),
    )


def _valid_id(user_id: Any) -> bool:
    return isinstance(user_id, str) and 0 < len(user_id) <= 128


def get_principal_sync(user_id: str) -> Principal | None:
    if not _valid_id(user_id):
        return None
    rows = _db.fetch_sync(_PRINCIPAL_SQL, (user_id,))
    return principal_from_row(rows[0]) if rows else None


async def get_principal(user_id: str) -> Principal | None:
    if not _valid_id(user_id):
        return None
    rows = await _db.fetch_async(_PRINCIPAL_SQL, (user_id,))
    return principal_from_row(rows[0]) if rows else None


class DemoLoginDisabled(PermissionError):
    pass


def _check_demo() -> None:
    # The persona picker is an identity bypass; it only exists in demo mode.
    if not settings().demo_login:
        raise DemoLoginDisabled("demo login is disabled (CORPAI_DEMO_LOGIN=0)")


def list_personas_sync() -> list[dict]:
    """Active persona users: user_id, name, title, dept, role, persona_key."""
    _check_demo()
    return [dict(r) for r in _db.fetch_sync(_PERSONAS_SQL)]


async def list_personas() -> list[dict]:
    _check_demo()
    return [dict(r) for r in await _db.fetch_async(_PERSONAS_SQL)]
