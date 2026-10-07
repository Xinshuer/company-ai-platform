"""Fixtures for the legal eval suite.

tests/conftest.py does not reach evals/, so this file prepares the same environment: a per-process
data dir under .data/test (never the development database), the fake LLM unless CORPAI_LLM=live,
and a database with the small synthetic company. When the test suite's own `pg` fixture already
started that database in this process (a full `python -m pytest` run), it is reused, not restarted.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("CORPAI_DATA_DIR", str(_ROOT / ".data" / "test" / str(os.getpid())))
if os.environ.get("CORPAI_LLM") != "live":
    os.environ["CORPAI_LLM"] = "fake"
os.environ.setdefault("CORPAI_EMBED", "fake")
os.environ.setdefault("CORPAI_DEMO_LOGIN", "1")

from corpai.config import reload_settings, settings  # noqa: E402

reload_settings()


@pytest.fixture(scope="session")
def anyio_backend():
    # psycopg async cannot run on the Proactor loop that Windows uses by default
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


@pytest.fixture(scope="session")
def legal_eval_db():
    from corpai.db import pg

    running = (settings().pg_dir / "postmaster.pid").exists()
    if not running:
        shutil.rmtree(settings().data_dir, ignore_errors=True)
    pg.ensure_server()
    if not running:
        from corpai.db.migrate import apply_migrations

        with pg.connect_owner() as conn:
            apply_migrations(conn)
    with pg.connect_owner() as conn:
        seeded = conn.execute("SELECT count(*) AS n FROM legal.contracts").fetchone()["n"] > 0
    if not seeded:
        from corpai import synth

        synth.run(seed=7, small=True)
    try:
        yield
    finally:
        if not running:
            pg.stop_server()
            shutil.rmtree(settings().data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def eval_contracts(legal_eval_db) -> dict[str, str]:
    from corpai.db import pg

    with pg.connect_owner() as conn:
        return {r["contract_id"]: r["text"] for r in conn.execute("SELECT contract_id, text FROM legal.contracts")}


@pytest.fixture(scope="session")
def eval_reviewer(legal_eval_db):
    """The head of legal: may read every contract in the golden set."""
    from corpai.identity import directory
    from corpai.synth.personas import PERSONAS

    p = directory.get_principal_sync(PERSONAS["legal_head"])
    assert p is not None
    return p
