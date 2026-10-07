"""Plain helpers for the RAG tests (imported as tests.rag.helpers)."""

from __future__ import annotations

import uuid
from typing import Any

from corpai.db import pg as pgmod
from corpai.types import Label, Level, Trust


def owner_rows(sql: str, params: Any = None) -> list[dict]:
    with pgmod.connect_owner() as conn:
        return conn.execute(sql, params).fetchall()


def fresh_id(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def label(level: int = 2, acl=("all",), trust: Trust = Trust.INTERNAL, **kw) -> Label:
    return Label(level=Level(level), acl_groups=frozenset(acl), trust=trust, **kw)
