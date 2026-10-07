"""Sortable unique ids: <prefix>_<10 chars of ms timestamp><10 random chars>, Crockford base32."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(7), "big")
    return f"{prefix}_{_b32(ms, 10)}{_b32(rand, 10)}"
