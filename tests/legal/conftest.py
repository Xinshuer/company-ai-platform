"""Fixtures for the M2 legal tests (the shared pg / seeded / personas / fake_llm come from tests/conftest.py)."""

from __future__ import annotations

import pytest

import corpai.legal  # noqa: F401  registers the legal FakeLLM responders


@pytest.fixture(scope="session")
def contracts(seeded) -> dict[str, dict]:
    """contract_id -> the stored legal.contracts row (owner connection: test setup only)."""
    from corpai.db import pg

    with pg.connect_owner() as conn:
        rows = conn.execute("SELECT * FROM legal.contracts ORDER BY contract_id").fetchall()
    return {r["contract_id"]: dict(r) for r in rows}


@pytest.fixture
def run_id() -> str:
    from corpai.ids import new_id

    return new_id("run")


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    from corpai.db import pg

    with pg.connect_owner() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def table_count(qualified: str) -> int | None:
    """Row count of a table, or None when the table does not exist (yet)."""
    from corpai.db import pg

    with pg.connect_owner() as conn:
        if conn.execute("SELECT to_regclass(%s) AS t", (qualified,)).fetchone()["t"] is None:
            return None
        return conn.execute(f"SELECT count(*) AS n FROM {qualified}").fetchone()["n"]  # name from the test, not user input


@pytest.fixture
def side_effects():
    """Snapshot of every place a write or send would land; call it again to compare."""
    tables = ("ops.proposals", "ops.outbox", "legal.contracts", "legal.contract_requests", "crm.interactions")

    def snap() -> dict[str, int | None]:
        return {t: table_count(t) for t in tables}

    return snap


@pytest.fixture
def owner_query():
    """Run a read query as the owner (test assertions only: audit rows, stored reviews)."""
    return owner_rows
