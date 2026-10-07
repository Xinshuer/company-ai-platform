"""Fixtures for the M3 report evals.

tests/conftest.py is not visible from evals/, so this file provides the same fixtures (pg, seeded,
personas, fake_llm, anyio_backend) with one difference: when the session already started and seeded a
database (pytest tests evals in one run), it reuses it instead of deleting the data dir under a running
server. Run order matters only in that case: `tests` before `evals` (the default testpaths order).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
os.environ["CORPAI_LLM"] = "fake"
os.environ["CORPAI_EMBED"] = "fake"
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.setdefault("CORPAI_DATA_DIR", str(_ROOT / ".data" / "test" / str(os.getpid())))
os.environ["CORPAI_DEMO_LOGIN"] = "1"

from corpai.config import reload_settings, settings  # noqa: E402

if Path(os.environ["CORPAI_DATA_DIR"]) != settings().data_dir:
    reload_settings()


@pytest.fixture(scope="session")
def anyio_backend():
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


def _server_running() -> bool:
    from corpai.db import pg as pgmod

    # tests/conftest's pg fixture registered this data dir when it started the server
    return Path(settings().pg_dir).resolve() in getattr(pgmod, "_base_uri", {})


@pytest.fixture(scope="session")
def pg():
    from corpai.db import pg as pgmod
    from corpai.db.migrate import apply_migrations

    owner = not _server_running()
    if owner:
        shutil.rmtree(settings().data_dir, ignore_errors=True)
    dsn = pgmod.ensure_server()
    with pgmod.connect_owner() as conn:
        apply_migrations(conn)
    yield dsn
    if owner:
        pgmod.stop_server()
        shutil.rmtree(settings().data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def seeded(pg):
    from corpai import synth
    from corpai.db import pg as pgmod

    with pgmod.connect_owner() as conn:
        done = conn.execute("SELECT count(*) AS n FROM synth.planted").fetchone()["n"] > 0
    if done:
        return None
    report = synth.run(seed=7, small=True)
    try:
        from corpai.rag import ingest_corpus
    except ImportError:
        ingest_corpus = None
    if ingest_corpus is not None:
        ingest_corpus(settings().corpus_dir)
    return report


@pytest.fixture(scope="session")
def personas(seeded):
    from corpai.identity import directory
    from corpai.synth.personas import PERSONAS

    out = {}
    for key, user_id in PERSONAS.items():
        p = directory.get_principal_sync(user_id)
        assert p is not None, f"persona {key} ({user_id}) missing from the directory"
        out[key] = p
    return out


@pytest.fixture
def fake_llm():
    from corpai.gateway import fake_llm as get_fake

    fake = get_fake()
    fake.calls.clear()
    yield fake
    fake.calls.clear()


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
