"""Local text embeddings (architecture §10): `get_embedder()` by settings().embed_mode.

    fake      -> FakeEmbedder  (feature hashing; deterministic; default and in every test)
    fastembed -> FastEmbedder  (paraphrase-multilingual-MiniLM-L12-v2, 384 dims, created lazily)
"""

from __future__ import annotations

import threading

from corpai.config import settings
from corpai.embed.base import DIM, Embedder, l2_normalise, to_pgvector
from corpai.embed.fake import FakeEmbedder
from corpai.embed.fast import MODEL_NAME, FastEmbedder

_lock = threading.Lock()
_instances: dict[str, Embedder] = {}


def get_embedder() -> Embedder:
    """One embedder per mode per process (the fastembed model is expensive to load)."""
    mode = settings().embed_mode
    with _lock:
        emb = _instances.get(mode)
        if emb is None:
            if mode == "fake":
                emb = FakeEmbedder()
            elif mode == "fastembed":
                emb = FastEmbedder()
            else:  # settings() already validates; keep the failure explicit anyway
                raise ValueError(f"unknown embed mode {mode!r}")
            _instances[mode] = emb
        return emb


__all__ = ["DIM", "Embedder", "FakeEmbedder", "FastEmbedder", "MODEL_NAME", "get_embedder", "l2_normalise",
           "to_pgvector"]
