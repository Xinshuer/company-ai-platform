"""Owner-side helpers for identity tests: insert directory rows with fresh ids."""

from __future__ import annotations

from corpai.db import pg
from corpai.ids import new_id


def add_user(*, dept: str = "sales", role: str = "employee", clearance: int = 3, manager_id: str | None = None,
             active: bool = True, persona_key: str | None = None, user_id: str | None = None,
             title: str = "Tester") -> str:
    uid = user_id or new_id("u")
    with pg.connect_owner() as c:
        c.execute("""INSERT INTO core.users (user_id, name, email, dept, role, title, manager_id, clearance,
                                             active, persona_key)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                  (uid, f"Test {uid}", f"{uid.replace(':', '-')}@kestrelbyte.example", dept, role, title,
                   manager_id, clearance, active, persona_key))
    return uid


def add_membership(user_id: str, grp: str) -> None:
    with pg.connect_owner() as c:
        c.execute("INSERT INTO core.memberships (user_id, grp) VALUES (%s, %s)", (user_id, grp))


def add_grant(user_id: str, grp: str, *, minutes: int = 60) -> str:
    gid = new_id("g")
    with pg.connect_owner() as c:
        c.execute("""INSERT INTO core.grants (grant_id, user_id, grp, reason, granted_by, expires_at)
                     VALUES (%s, %s, %s, 'test ticket', 'tester', now() + make_interval(mins => %s))""",
                  (gid, user_id, grp, minutes))
    return gid


def owner_exec(sql: str, params=()) -> None:
    with pg.connect_owner() as c:
        c.execute(sql, params)
