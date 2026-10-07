"""Thread ownership (ops.threads, migration 100). Every call runs in principal_tx as the caller,
and the SQL functions take the owner from the session, never from a parameter."""

from __future__ import annotations

import psycopg

from corpai.db import pg
from corpai.types import Principal


class ThreadError(Exception):
    pass


async def create(p: Principal, thread_id: str, *, agent: str, app: str) -> dict:
    try:
        async with pg.principal_tx(p) as conn:
            cur = await conn.execute("SELECT * FROM ops.thread_create(%s, %s, %s)", (thread_id, agent, app))
            return dict(await cur.fetchone())
    except psycopg.errors.InsufficientPrivilege:
        raise ThreadError("only an active user may open a thread") from None


async def get_own(p: Principal, thread_id: str) -> dict | None:
    """The caller's own thread, or None (unknown and someone else's look the same)."""
    if not isinstance(thread_id, str) or not thread_id.startswith("thr_") or len(thread_id) > 64:
        return None
    async with pg.principal_tx(p) as conn:
        cur = await conn.execute("SELECT * FROM ops.threads WHERE thread_id = %s", (thread_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def add_run(p: Principal, run_id: str, thread_id: str) -> None:
    try:
        async with pg.principal_tx(p, run_id=run_id) as conn:
            await conn.execute("SELECT ops.thread_run_add(%s, %s)", (run_id, thread_id))
    except psycopg.errors.InsufficientPrivilege:
        raise ThreadError("not your thread") from None


async def for_proposal(p: Principal, proposal_id: str) -> dict | None:
    """The requester's thread waiting on `proposal_id`, for its approver or requester; else None."""
    async with pg.principal_tx(p) as conn:
        cur = await conn.execute("SELECT * FROM ops.thread_for_proposal(%s)", (proposal_id,))
        row = await cur.fetchone()
    return dict(row) if row else None
