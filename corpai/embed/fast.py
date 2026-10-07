"""Multilingual sentence embedder (fastembed, ONNX on CPU). Local: queries and documents never leave the process.

The model is created on first use, because loading it takes seconds and downloads it into
settings().cache_dir/'fastembed' (inside the project, on G:) the first time.
"""

from __future__ import annotations

import threading

from corpai.config import settings
from corpai.embed.base import DIM, l2_normalise

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class FastEmbedder:
    name = MODEL_NAME
    dim = DIM

    def __init__(self, cache_dir: str | None = None) -> None:
        self._cache_dir = cache_dir or str(settings().cache_dir / "fastembed")
        self._model = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(MODEL_NAME, cache_dir=self._cache_dir)
            return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out = []
        for vec in self._get().embed(list(texts)):
            values = [float(x) for x in vec]
            if len(values) != DIM:
                raise ValueError(f"{MODEL_NAME} returned {len(values)} dimensions, expected {DIM}")
            out.append(l2_normalise(values))
        return out
