"""Reading traces. Each reader is enforced by the database, not only here (migrations/002)."""

from __future__ import annotations

from datetime import datetime

import psycopg

from corpai.types import Principal, Role

_SPAN_COLUMNS = ("span_id, run_id, parent_id, name, kind, user_id, dept, level, started_at, ended_at, "
                 "status, attrs, input_text, output_text")


async def read_run(principal: Principal, run_id: str) -> list[dict]:
    """The principal's own spans of one run (RLS: user_id = app.user_id). Others' runs → []."""
    from corpai.db import pg

    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute(
            f"SELECT {_SPAN_COLUMNS} FROM trace.spans WHERE run_id = %s ORDER BY started_at, span_id",
            (run_id,))
        return [dict(r) for r in await cur.fetchall()]


async def read_meta(principal: Principal, run_id: str) -> list[dict]:
    """Platform admin: one run's span metadata, without input/output text."""
    from corpai.db import pg

    if not (principal.active and principal.role == Role.PLATFORM_ADMIN):
        raise PermissionError("only the platform admin may read trace metadata")
    try:
        async with pg.principal_tx(principal) as conn:
            cur = await conn.execute("SELECT * FROM trace.read_meta(%s)", (run_id,))
            return [dict(r) for r in await cur.fetchall()]
    except psycopg.errors.InsufficientPrivilege as e:
        raise PermissionError("only the platform admin may read trace metadata") from e


async def dept_stats(principal: Principal, since: datetime) -> list[dict]:
    """Heads: span counts by kind/route/level/status for their own dept only, no text.

    The same rule as decide(p, READ, trace) (trace-head-aggregate: head:<dept> of the run's dept),
    so the CEO, head of exec, gets exec's counts and nothing company-wide (design-review 五).
    Rows with fewer than 5 spans or fewer than 5 distinct people are not returned (migration 102),
    so a small department's statistics do not point at one person.
    """
    from corpai.db import pg

    if not (principal.active and principal.role in (Role.MANAGER, Role.CEO)
            and f"head:{principal.dept}" in principal.groups):
        raise PermissionError("only department heads may read trace statistics")
    try:
        async with pg.principal_tx(principal) as conn:
            cur = await conn.execute("SELECT * FROM trace.dept_stats(%s)", (since,))
            return [dict(r) for r in await cur.fetchall()]
    except psycopg.errors.InsufficientPrivilege as e:
        raise PermissionError("only department heads may read trace statistics") from e
