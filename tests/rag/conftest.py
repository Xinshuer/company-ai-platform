"""Fixtures for the RAG tests (stream B1). The shared `seeded` fixture runs ingest_corpus once per session."""

from __future__ import annotations

import pytest

from corpai.db import pg as pgmod


@pytest.fixture
def gazetteer(seeded, monkeypatch):
    """A loaded gazetteer for this test; the process-global state is restored afterwards."""
    from corpai.classify import gazetteer_size, load_gazetteer_from_db
    from corpai.classify import names as names_mod

    monkeypatch.setattr(names_mod, "_state", names_mod._state)
    if gazetteer_size() == 0:
        load_gazetteer_from_db()
    return gazetteer_size()


@pytest.fixture
def cleanup_docs(seeded):
    """Collects doc_ids a test creates and deletes them (chunks cascade) at the end."""
    ids: list[str] = []
    yield ids
    if ids:
        with pgmod.connect_owner() as conn:
            conn.execute("DELETE FROM rag.documents WHERE doc_id = ANY(%s)", (ids,))
