"""Fixtures for the M3 report evals.

pg, seeded, personas, fake_llm and anyio_backend are the shared fixtures of tests/conftest.py, which
the root conftest.py makes available here (issues-log D-11), so `pytest tests evals` and
`pytest evals tests` share one database in either order.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def planted(seeded) -> dict[str, dict]:
    """The evaluation answers, read as the owner. Only evals read them; the app never can."""
    from corpai.db import pg as pgmod

    with pgmod.connect_owner() as conn:
        rows = conn.execute("SELECT kind, ref, expected FROM synth.planted").fetchall()
    return {r["kind"]: {"ref": r["ref"], **r["expected"]} for r in rows}


@pytest.fixture(scope="session")
def refreshed(seeded):
    from corpai.metrics import refresh_snapshots

    return {p: refresh_snapshots(p) for p in ("2026-09", "2026-Q3")}
