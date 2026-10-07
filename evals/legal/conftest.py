"""Fixtures for the legal eval suite.

The database, the seeded company and the fake/live model mode come from the shared fixtures of
tests/conftest.py, which the root conftest.py makes available here (issues-log D-11). Live mode:
`CORPAI_LLM=live CORPAI_EVAL_RUNS=3 python -m pytest evals/legal -m eval` (eval paths only).
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def legal_eval_db(seeded):
    """The small synthetic company (seed 7) in the session database."""
    return seeded


@pytest.fixture(scope="session")
def eval_contracts(legal_eval_db) -> dict[str, str]:
    from corpai.db import pg

    with pg.connect_owner() as conn:
        return {r["contract_id"]: r["text"] for r in conn.execute("SELECT contract_id, text FROM legal.contracts")}


@pytest.fixture(scope="session")
def eval_reviewer(personas):
    """The head of legal: may read every contract in the golden set."""
    return personas["legal_head"]
