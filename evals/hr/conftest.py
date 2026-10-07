"""Fixtures for the HR screening eval.

evals/ does not see tests/conftest.py, so this file brings its own database fixture. It **attaches**
to the session database when tests/ already started one in the same pytest process (same per-pid
data dir), and otherwise starts a fresh one under .data/test/<pid>, seeds the small company and
removes it at the end — never the developer database in .data/pg.

The model mode is whatever CORPAI_LLM says (fake by default). A live run is
`CORPAI_LLM=live python -m pytest evals/hr -m eval -s` on its own: when tests/ is collected in the
same run, tests/conftest.py forces the fake model for the whole process.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("CORPAI_DATA_DIR", str(_ROOT / ".data" / "test" / str(os.getpid())))

from corpai.config import reload_settings, settings  # noqa: E402

reload_settings()


def pytest_collection_modifyitems(session, config, items):
    # tests/conftest.py's `pg` fixture wipes the per-pid data dir when it starts. Running this eval
    # last means that fixture (if collected) has already started the server we then attach to,
    # whatever order the paths were given in on the command line.
    here = Path(__file__).resolve().parent
    mine = {id(i) for i in items if here in Path(str(i.path)).resolve().parents}
    if mine and len(mine) != len(items):
        items[:] = [i for i in items if id(i) not in mine] + [i for i in items if id(i) in mine]


@pytest.fixture(scope="session")
def anyio_backend():
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


@pytest.fixture(scope="session")
def hr_eval_db():
    from corpai import synth
    from corpai.db import pg
    from corpai.db.migrate import apply_migrations

    data_dir = settings().data_dir
    assert data_dir.resolve() != (_ROOT / ".data").resolve(), "the eval never touches the developer database"
    attached = (settings().pg_dir / "postmaster.pid").exists()
    if not attached:
        shutil.rmtree(data_dir, ignore_errors=True)
    pg.ensure_server()
    with pg.connect_owner() as conn:
        apply_migrations(conn)
        seeded = conn.execute("SELECT count(*) AS n FROM core.users").fetchone()["n"] > 0
    if not seeded:
        synth.run(seed=7, small=True)
    with pg.connect_owner() as conn:
        conn.execute("UPDATE ops.switches SET \"on\" = false WHERE name IN ('local_down', 'external_down')")
    yield
    with pg.connect_owner() as conn:  # leave hr.screenings as the seed has it (other suites check that)
        conn.execute("DELETE FROM hr.screenings")
        conn.execute("DELETE FROM hr.screening_evidence")
    if not attached:
        pg.stop_server()
        shutil.rmtree(data_dir, ignore_errors=True)
