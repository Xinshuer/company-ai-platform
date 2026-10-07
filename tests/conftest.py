"""Shared fixtures (frozen core; docs/ENGINEERING.md "Tests").

Every test session:
- uses the fake LLM and the fake embedder, and removes DEEPSEEK_API_KEY so nothing can call the paid API;
- gets its own data dir (.data/test/<pid>) so parallel pytest runs never share a database;
- stops its postgres with pg_ctl and deletes the data dir at the end.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
os.environ["CORPAI_LLM"] = "fake"
# CORPAI_TEST_FASTEMBED=1 opts a run into the real embedding model (tests/embed/test_fastembed.py)
os.environ["CORPAI_EMBED"] = "fastembed" if os.environ.get("CORPAI_TEST_FASTEMBED") == "1" else "fake"
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.setdefault("CORPAI_DATA_DIR", str(_ROOT / ".data" / "test" / str(os.getpid())))
os.environ["CORPAI_DEMO_LOGIN"] = "1"

from corpai.config import reload_settings, settings  # noqa: E402

reload_settings()


@pytest.fixture(scope="session")
def anyio_backend():
    # psycopg async cannot run on the Proactor loop that Windows uses by default
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


@pytest.fixture(scope="session")
def pg():
    """A fresh postgres for this session with all migrations applied. Yields the owner DSN."""
    from corpai.db import pg as pgmod

    data_dir = settings().data_dir
    shutil.rmtree(data_dir, ignore_errors=True)
    dsn = pgmod.ensure_server()
    try:
        from corpai.db.migrate import apply_migrations
    except ImportError:  # before stream A1 lands
        apply_migrations = None
    if apply_migrations is not None:
        with pgmod.connect_owner() as conn:
            apply_migrations(conn)
    yield dsn
    pgmod.stop_server()
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def seeded(pg):
    """The small synthetic company (seed 7) in the DB, with its corpus ingested into RAG."""
    from corpai import synth

    report = synth.run(seed=7, small=True)
    try:
        from corpai.rag import ingest_corpus
    except ImportError:  # before stream B1 lands
        ingest_corpus = None
    if ingest_corpus is not None:
        ingest_corpus(settings().corpus_dir)
    return report


@pytest.fixture(scope="session")
def personas(seeded):
    """name -> Principal, e.g. personas["sales_staff"], personas["finance_head"], personas["auditor"]."""
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


@pytest.fixture
async def app_client(seeded):
    """httpx2 client on the ASGI app (lifespan run, MCP servers mounted). Use with @pytest.mark.anyio."""
    import httpx2

    from corpai.api.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            yield client
