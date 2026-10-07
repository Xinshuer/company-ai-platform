"""LangGraph checkpoints in schema lg (architecture §12).

The owner creates and upgrades the tables (AsyncPostgresSaver.setup) at startup, grants the app
role DML on exactly those tables and puts them under row-level security: a checkpoint row is
visible and writable only while the session's principal owns its thread (ops.thread_is_mine,
migration 100). Checkpoints hold retrieved text and tool results up to L4, so the app role with
no principal, or with someone else's, must see nothing, like every other content table (§4).

Request paths therefore never share one saver: CheckpointStore.for_principal(p) takes one
app-role connection (search_path=lg) for the whole run, sets the principal's session variables on
it (session level: the saver runs in autocommit) and hands out an AsyncPostgresSaver bound to that
connection; the pool's reset callback wipes the variables when the connection goes back.
Checkpoints hold graph state only: RunContext (principal, services) is passed as runtime context
and never serialised, and no helper puts a token into the state.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from corpai.db import pg
from corpai.types import Principal

log = logging.getLogger(__name__)

LG_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
_KW = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, "options": "-c search_path=lg"}


async def setup_schema() -> None:
    """Owner job at startup: create / migrate the lg tables, then grant the app role DML on them."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conn = await AsyncConnection.connect(pg.owner_dsn(), **_KW)
    async with conn:
        await AsyncPostgresSaver(conn).setup()
        for table in LG_TABLES:
            # table names are the constants above, never input
            await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON lg.{table} TO corpai_app")
            await conn.execute(f"ALTER TABLE lg.{table} ENABLE ROW LEVEL SECURITY")
            await conn.execute(f"DROP POLICY IF EXISTS lg_own_thread ON lg.{table}")
            await conn.execute(f"CREATE POLICY lg_own_thread ON lg.{table} FOR ALL TO corpai_app"
                               " USING (ops.thread_is_mine(thread_id)) WITH CHECK (ops.thread_is_mine(thread_id))")
        await conn.execute("GRANT SELECT ON lg.checkpoint_migrations TO corpai_app")


_VARS = pg.SESSION_VARS


def _session_vars(p: Principal, run_id: str | None) -> list[tuple[str, str]]:
    """Exactly what principal_tx sets (pg.session_vars); deactivated users get no groups and
    clearance 0, so RLS (and ops.thread_is_mine) shows them nothing."""
    return pg.session_vars(p, run_id)


async def _wipe(conn: AsyncConnection) -> None:
    for name in _VARS:
        await conn.execute("SELECT set_config(%s, '', false)", (name,))


class CheckpointStore:
    """Hands each run a saver whose connection carries that run's principal (see module doc)."""

    def __init__(self, pool: Any):
        self.pool = pool

    @asynccontextmanager
    async def for_principal(self, p: Principal, *, run_id: str | None = None) -> AsyncIterator[Any]:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with self.pool.connection() as conn:
            await _wipe(conn)  # never trust what a previous user may have left behind
            for name, value in _session_vars(p, run_id):
                await conn.execute("SELECT set_config(%s, %s, false)", (name, value))
            yield AsyncPostgresSaver(conn)


@asynccontextmanager
async def open_checkpointer(*, max_size: int = 16) -> AsyncIterator[CheckpointStore]:
    """A CheckpointStore on an app-role pool (search_path=lg). Call setup_schema() first. One
    connection is held per running graph, so max_size bounds concurrent runs (others wait)."""
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(pg.app_dsn(), min_size=1, max_size=max_size, open=False, kwargs=dict(_KW),
                               reset=_wipe)
    await pool.open()
    try:
        yield CheckpointStore(pool)
    finally:
        await pool.close()


def prune_sync(days: int) -> dict[str, int]:
    """Owner job: delete threads whose newest checkpoint is older than `days` days (and threads
    that never got a checkpoint and were opened that long ago). Their ops.threads rows go too, so
    a pruned thread cannot be continued with an empty state and a reset hwm."""
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValueError("days must be a positive integer")
    with pg.connect_owner() as conn:
        has_lg = conn.execute("SELECT to_regclass('lg.checkpoints') IS NOT NULL AS ok").fetchone()["ok"]
        stale: list[str] = []
        if has_lg:
            stale = [r["thread_id"] for r in conn.execute(
                "SELECT thread_id FROM lg.checkpoints GROUP BY thread_id "
                "HAVING max((checkpoint ->> 'ts')::timestamptz) < now() - make_interval(days => %s)",
                (days,)).fetchall()]
        orphans = [r["thread_id"] for r in conn.execute(
            "SELECT t.thread_id FROM ops.threads t WHERE t.created_at < now() - make_interval(days => %s)"
            + (" AND NOT EXISTS (SELECT 1 FROM lg.checkpoints c WHERE c.thread_id = t.thread_id)" if has_lg else ""),
            (days,)).fetchall()]
        counts = {"threads": 0, "checkpoints": 0, "blobs": 0, "writes": 0}
        if stale and has_lg:
            counts["checkpoints"] = conn.execute("DELETE FROM lg.checkpoints WHERE thread_id = ANY(%s)", (stale,)).rowcount
            counts["blobs"] = conn.execute("DELETE FROM lg.checkpoint_blobs WHERE thread_id = ANY(%s)", (stale,)).rowcount
            counts["writes"] = conn.execute("DELETE FROM lg.checkpoint_writes WHERE thread_id = ANY(%s)",
                                            (stale,)).rowcount
        gone = sorted(set(stale) | set(orphans))
        if gone:
            counts["threads"] = conn.execute("DELETE FROM ops.threads WHERE thread_id = ANY(%s)", (gone,)).rowcount
        counts["pruned_threads"] = len(gone)
    return counts
