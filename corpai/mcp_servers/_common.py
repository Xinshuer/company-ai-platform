"""Argument checks and the database helper shared by the domain servers.

Ids are checked against their format before they touch SQL or the audit log: tool arguments come
from a model and may carry anything (an injected instruction, a token-looking string).
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, AsyncIterator, Iterable

import psycopg

from corpai.db import pg
from corpai.mcp_servers._envelope import ToolArgError

ID_PATTERNS: dict[str, re.Pattern] = {
    "account_id": re.compile(r"^A-[0-9]{4}$"),
    "opp_id": re.compile(r"^O-[0-9]{4}$"),
    "contact_id": re.compile(r"^CT-[0-9]{5}$"),
    "project_id": re.compile(r"^P-[0-9]{2}$"),
    "ticket_id": re.compile(r"^FP-[0-9]{1,6}$"),
    "contract_id": re.compile(r"^C-[0-9]{4}$"),
    "req_id": re.compile(r"^R-[0-9]{2}$"),
    "employee_id": re.compile(r"^u[0-9]{4}$"),
    "candidate_id": re.compile(r"^CAND-[0-9]{3,6}$"),
    "invoice_id": re.compile(r"^INV-[0-9]{5}$"),
    "doc_id": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),  # rag.documents CHECK
    "account_code": re.compile(r"^[0-9]{4}$"),
}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.example$")
_PERIOD = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
DEPTS = ("sales", "eng", "legal", "finance", "hr", "exec", "it")


def check_id(kind: str, value: Any) -> str:
    if not isinstance(value, str) or not ID_PATTERNS[kind].match(value):
        raise ToolArgError(f"{kind} is not a valid id")
    return value


def opt_id(kind: str, value: Any) -> str | None:
    return None if value in (None, "") else check_id(kind, value)


def check_choice(name: str, value: Any, choices: Iterable[str]) -> str:
    choices = tuple(choices)
    if value not in choices:
        raise ToolArgError(f"{name} must be one of: {', '.join(choices)}")
    return value


def opt_choice(name: str, value: Any, choices: Iterable[str]) -> str | None:
    return None if value in (None, "") else check_choice(name, value, choices)


def check_text(name: str, value: Any, *, max_len: int, min_len: int = 1) -> str:
    if not isinstance(value, str) or not (min_len <= len(value.strip()) <= max_len):
        raise ToolArgError(f"{name} must be text of {min_len}-{max_len} characters")
    return value.strip()


def search_text(value: Any, *, max_len: int = 200) -> str:
    """Free-text filter for ILIKE: length-capped, LIKE wildcards escaped (never formatted into SQL)."""
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > max_len:
        raise ToolArgError(f"query must be text of at most {max_len} characters")
    return value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def check_limit(value: Any, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolArgError("limit must be a positive integer")
    return min(value, maximum)


def check_email(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _EMAIL.match(value.strip()):
        raise ToolArgError(f"{name} must be an e-mail address in the simulated world (*.example)")
    return value.strip()


def check_period(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _PERIOD.match(value):
        raise ToolArgError(f"{name} must look like YYYY-MM")
    return value


def check_date(name: str, value: Any) -> date:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else _bad(name)
    except ValueError:
        _bad(name)


def _bad(name: str):
    raise ToolArgError(f"{name} must be a date YYYY-MM-DD")


@asynccontextmanager
async def tx(col) -> AsyncIterator[psycopg.AsyncConnection]:
    """One principal transaction for the caller of this tool call (RLS applies)."""
    async with pg.principal_tx(col.auth.principal, run_id=col.auth.run_id) as conn:
        yield conn


async def fetch_all(conn: psycopg.AsyncConnection, sql: str, params: Iterable[Any] = ()) -> list[dict]:
    cur = await conn.execute(sql, tuple(params))
    return list(await cur.fetchall())


async def fetch_one(conn: psycopg.AsyncConnection, sql: str, params: Iterable[Any] = ()) -> dict | None:
    cur = await conn.execute(sql, tuple(params))
    return await cur.fetchone()
