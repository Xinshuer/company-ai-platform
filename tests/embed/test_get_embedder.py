"""get_embedder() follows settings().embed_mode; the fastembed model is only created on first use."""

from __future__ import annotations

import pytest

from corpai.config import reload_settings
from corpai.embed import FakeEmbedder, FastEmbedder, get_embedder


@pytest.fixture
def mode(monkeypatch):
    def _set(value: str):
        monkeypatch.setenv("CORPAI_EMBED", value)
        return reload_settings()
    yield _set
    monkeypatch.setenv("CORPAI_EMBED", "fake")
    reload_settings()


def test_fake_mode_is_the_test_default():
    emb = get_embedder()
    assert isinstance(emb, FakeEmbedder) and emb is get_embedder()


def test_fastembed_mode_is_lazy(mode):
    mode("fastembed")
    emb = get_embedder()
    assert isinstance(emb, FastEmbedder)
    assert emb._model is None  # no model load (and no download) until embed() is called
    assert emb._cache_dir.replace("\\", "/").endswith(".cache/fastembed")


def test_unknown_mode_is_refused(mode):
    with pytest.raises(ValueError):
        mode("openai")
