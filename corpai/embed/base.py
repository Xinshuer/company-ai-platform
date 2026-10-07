"""The embedder contract shared by the fake and the real model (architecture §10)."""

from __future__ import annotations

import math
from typing import Protocol, Sequence, runtime_checkable

DIM = 384  # rag.chunks.embedding is vector(384); both embedders must produce exactly this


@runtime_checkable
class Embedder(Protocol):
    name: str  # stored with every ingested document, so a model switch triggers re-embedding
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def l2_normalise(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        raise ValueError("cannot normalise a zero vector")
    return [x / norm for x in vec]


def to_pgvector(vec: Sequence[float]) -> str:
    """Text form of a pgvector value ('[0.1,0.2,...]'); there is no pgvector Python adapter in the lock file."""
    if len(vec) != DIM:
        raise ValueError(f"embedding has {len(vec)} dimensions, expected {DIM}")
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"
