"""Real multilingual embeddings (slow, opt-in).

tests/conftest.py forces CORPAI_EMBED=fake for the whole run, so this test is switched on separately:
    CORPAI_TEST_FASTEMBED=1 python -m pytest tests/embed -m slow
It never downloads: it skips unless the model is already in .cache/fastembed (populated by running
`CORPAI_EMBED=fastembed python -m corpai ingest` once).
"""

from __future__ import annotations

import os

import pytest

from corpai.config import settings
from corpai.embed import FastEmbedder

pytestmark = pytest.mark.slow


def _model_cached() -> bool:
    root = settings().cache_dir / "fastembed"
    return root.is_dir() and any("paraphrase-multilingual-minilm-l12-v2" in p.name.lower() for p in root.iterdir())


@pytest.mark.skipif(os.environ.get("CORPAI_TEST_FASTEMBED") != "1", reason="opt-in: CORPAI_TEST_FASTEMBED=1")
def test_chinese_question_finds_english_document():
    if not _model_cached():
        pytest.skip("fastembed model not cached; run `CORPAI_EMBED=fastembed python -m corpai ingest` once")
    emb = FastEmbedder()
    q, travel, adr = emb.embed(["差旅政策是什么？", "Travel policy: book trains for trips under 600 km.",
                                "ADR 001: the optimisation engine stores routes in Postgres."])
    cos = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731 (vectors are unit length)
    assert len(q) == 384
    assert cos(q, travel) > cos(q, adr) + 0.1
