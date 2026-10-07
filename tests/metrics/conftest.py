"""Fixtures for the metric-layer tests.

Each test that changes snapshot state works on its own period, so tests never depend on order:
  2026-09, 2026-Q3   drafts only (never signed)                       -> `drafts`
  2026-08            refreshed, signed and published as the CFO       -> `published`
  other months       owned by a single test (sign/publish, versions)
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

import pytest

DRAFT_PERIODS = ("2026-09", "2026-Q3")
PUBLISHED_PERIOD = "2026-08"


def run(coro: Coroutine) -> Any:
    """Run a coroutine from sync code on the Selector loop (psycopg async needs it on Windows)."""
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


@pytest.fixture(scope="session")
def drafts(seeded):
    from corpai.metrics import refresh_snapshots

    return {p: refresh_snapshots(p) for p in DRAFT_PERIODS}


@pytest.fixture(scope="session")
def published(seeded, personas):
    from corpai.metrics import load_metrics, publish, refresh_snapshots, sign_off

    refresh_snapshots(PUBLISHED_PERIOD)
    ids = list(load_metrics())
    cfo = personas["finance_head"]
    signed = run(sign_off(cfo, PUBLISHED_PERIOD, ids))
    pub = run(publish(cfo, PUBLISHED_PERIOD, ids))
    return {"signed": signed, "published": pub}


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    from corpai.db import pg

    with pg.connect_owner() as c:
        return c.execute(sql, params).fetchall()


def rls_rows(principal, sql: str, params: tuple = ()) -> list[dict]:
    from corpai.db import pg

    with pg.principal_tx_sync(principal) as c:
        return c.execute(sql, params).fetchall()
