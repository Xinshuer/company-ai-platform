"""Fixtures for the HR screening eval.

The database and the seeded small company are the shared fixtures of tests/conftest.py, which the
root conftest.py makes available here (issues-log D-11). The model mode is fake unless the run asks
for the live model with eval paths only: `CORPAI_LLM=live python -m pytest evals/hr -m eval -s`.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def hr_eval_db(seeded):
    from corpai.db import pg

    with pg.connect_owner() as conn:
        conn.execute("UPDATE ops.switches SET \"on\" = false WHERE name IN ('local_down', 'external_down')")
    yield
    with pg.connect_owner() as conn:  # leave hr.screenings as the seed has it (other suites check that)
        conn.execute("DELETE FROM hr.screenings")
        conn.execute("DELETE FROM hr.screening_evidence")
