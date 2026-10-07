"""Gateway state in the `ops` schema: outage switches, token budgets, external egress log.

All access goes through the SECURITY DEFINER functions of migration 010 as corpai_app. Budget
and egress calls run inside principal_tx, so the database takes the department and user from the
session variables instead of trusting parameters.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

from corpai import audit, clock
from corpai.db import pg
from corpai.types import Principal, Role

SWITCHES = ("local_down", "external_down")


async def _fetch_app(sql: str, params: tuple = ()) -> list[dict]:
    """A principal-less app-role query (the switches carry no business content)."""
    async with pg.app_tx() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def read_switches() -> list[dict]:
    """Full rows (name, on, reason, updated_by, updated_at) for the admin page."""
    return [dict(r) for r in await _fetch_app("SELECT * FROM ops.get_switches()")]


async def get_switches() -> dict[str, bool]:
    """{switch name: on}. A switch missing from the table counts as ON for local_down: if we cannot
    tell whether the local model is up, L3/L4 requests must not proceed (fail closed)."""
    rows = {r["name"]: bool(r["on"]) for r in await read_switches()}
    return {name: rows.get(name, name == "local_down") for name in SWITCHES}


async def set_switch(principal: Principal, name: str, on: bool, reason: str) -> dict:
    """Flip an outage switch. Platform admin only (checked here and in SQL); every attempt is
    audited as outage_switch, a refused one with decision=deny."""
    allowed = principal.active and principal.role == Role.PLATFORM_ADMIN
    if name not in SWITCHES or not isinstance(on, bool) or not isinstance(reason, str) or not reason.strip():
        raise ValueError("set_switch needs a known switch name, on=True/False and a reason")
    if not allowed:
        await audit.arecord("outage_switch", actor=principal.user_id, action="set", resource=f"switch:{name}",
                            decision="deny", reason="not_platform_admin", details={"on": on})
        raise PermissionError("only the platform admin may change outage switches")
    try:
        async with pg.principal_tx(principal) as conn:
            cur = await conn.execute("SELECT * FROM ops.set_switch(%s, %s, %s)", (name, on, reason))
            row = await cur.fetchone()
            # Audited inside the transaction: if the audit write fails, the switch does not change.
            await audit.arecord("outage_switch", actor=principal.user_id, action="set",
                                resource=f"switch:{name}", decision="allow", reason=reason[:500],
                                details={"on": on})
    except psycopg.errors.InsufficientPrivilege as e:
        raise PermissionError("only the platform admin may change outage switches") from e
    return dict(row)


def budget_day() -> date:
    """The budget window: the wall-clock UTC date, the same day ops.budget_day() computes in SQL.

    Not the simulation date: clock.today() is fixed (CORPAI_SIM_TODAY), so a per-sim-day budget
    would never reset. The budget limits real spend, which happens on real days. The database
    decides the day itself; this helper only tells readers (tests, the admin page) which row it is.
    """
    return clock.now_utc().date()


async def reserve_and_log(principal: Principal, *, run_id: str, tokens: int,
                          egress: dict[str, Any] | None) -> tuple[int, int | None, int | None]:
    """Reserve `tokens` of the department's budget and, for external calls, append the egress row,
    in one transaction. Returns (tokens left, reservation id, egress id). tokens left < 0 means the
    budget is exhausted: nothing was reserved and nothing was logged (the call must not be made)."""
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute("SELECT remaining, reservation_id FROM ops.consume_budget(%s, %s::bigint)",
                                 (str(principal.dept), int(tokens)))
        row = await cur.fetchone()
        left, reservation = int(row["remaining"]), row["reservation_id"]
        if left < 0 or egress is None:
            return left, reservation, None
        cur = await conn.execute(
            "SELECT ops.log_egress(%s, %s, %s::smallint, %s, %s) AS id",
            (egress["destination"], egress["route"], int(egress["level"]), run_id, egress["payload"]))
        return left, reservation, int((await cur.fetchone())["id"])


async def settle(principal: Principal, *, run_id: str, reservation_id: int, actual: int) -> int | None:
    """Replace one reservation with the real usage (0 when the call never happened)."""
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute("SELECT ops.settle_budget(%s::bigint, %s::bigint) AS remaining",
                                 (int(reservation_id), int(actual)))
        row = await cur.fetchone()
        return None if row["remaining"] is None else int(row["remaining"])
