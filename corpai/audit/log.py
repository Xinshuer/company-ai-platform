"""Writing and reading the audit log (docs/architecture.md §9).

Every write opens its own short-lived autocommit connection as corpai_app and calls the
SECURITY DEFINER function audit.append(). Because the entry commits on its own, a denial or an
error is still on record when the business transaction around it rolls back.
"""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from typing import Any, Mapping, Sequence

import psycopg

from corpai.audit.credentials import check_no_credentials
from corpai.types import Principal, Role

EVENTS: frozenset[str] = frozenset({
    "login", "demo_login", "policy_decision", "tool_call", "retrieval", "model_call",
    "route_decision", "proposal_created", "proposal_approved", "proposal_rejected",
    "proposal_executed", "export", "outage_switch", "budget_exceeded", "break_glass", "owner_job",
    "audit_read",  # the security auditor's own reads of the log (GET /api/audit; migration 101)
})
ROUTES: frozenset[str] = frozenset({"local", "external"})

_APPEND_SQL = (
    "SELECT audit.append(%s::text, %s::text, %s::text[], %s::text, %s::text, %s::text, %s::text, "
    "%s::text, %s::smallint, %s::text, %s::text, %s::jsonb)"
)
_JOB_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

# Each async write holds one connection for a few ms; cap them per event loop so a burst of
# audit events cannot exhaust Postgres max_connections next to the API pool.
_MAX_CONCURRENT = 16
_sems: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _sems.get(loop)
    if sem is None:
        sem = _sems[loop] = asyncio.Semaphore(_MAX_CONCURRENT)
    return sem


def _opt_str(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"audit {name} must be a string, got {type(value).__name__}")
    return str(value)


def _params(event: str, actor: str, act_chain: Sequence[str], action: str | None, resource: str | None,
            decision: str | None, reason: str | None, route: str | None, level: int | None,
            approval_id: str | None, run_id: str | None, details: Mapping | None) -> tuple:
    """Validate everything before touching the database; returns the audit.append() arguments."""
    if event not in EVENTS:
        raise ValueError(f"unknown audit event {event!r}; allowed: {sorted(EVENTS)}")
    if not isinstance(actor, str) or not actor:
        raise ValueError("audit actor must be a non-empty string")
    if isinstance(act_chain, str):  # a bare string would silently become a list of characters
        raise TypeError("act_chain must be a sequence of strings, not a string")
    chain = [str(a) for a in act_chain]
    if any(not a for a in chain):
        raise ValueError("act_chain entries must be non-empty strings")
    if route is not None and str(route) not in ROUTES:
        raise ValueError(f"route must be one of {sorted(ROUTES)}")
    if level is not None:
        if isinstance(level, bool) or int(level) not in (1, 2, 3, 4):
            raise ValueError("level must be 1..4")
        level = int(level)
    if details is None:
        details = {}
    if not isinstance(details, Mapping):
        raise TypeError("audit details must be a mapping")
    check_no_credentials(details, "details")
    for name, text in (("reason", reason), ("resource", resource), ("action", action)):
        if text is not None:
            check_no_credentials(text, name)
    try:
        details_json = json.dumps(dict(details), ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as e:
        raise ValueError(f"audit details must be JSON-serialisable: {e}") from None
    return (event, actor, chain, _opt_str("action", action), _opt_str("resource", resource),
            _opt_str("decision", decision), _opt_str("reason", reason),
            None if route is None else str(route), level, _opt_str("approval_id", approval_id),
            _opt_str("run_id", run_id), details_json)


def record(event: str, *, actor: str, act_chain: Sequence[str] = (), action: str | None = None,
           resource: str | None = None, decision: str | None = None, reason: str | None = None,
           route: str | None = None, level: int | None = None, approval_id: str | None = None,
           run_id: str | None = None, details: Mapping | None = None) -> int:
    """Append one audit entry and return its seq. Commits on its own connection."""
    from corpai.db import pg

    params = _params(event, actor, act_chain, action, resource, decision, reason, route, level,
                     approval_id, run_id, details)
    with psycopg.connect(pg.app_dsn(), autocommit=True) as conn:
        row = conn.execute(_APPEND_SQL, params).fetchone()
    return int(row[0])


async def arecord(event: str, *, actor: str, act_chain: Sequence[str] = (), action: str | None = None,
                  resource: str | None = None, decision: str | None = None, reason: str | None = None,
                  route: str | None = None, level: int | None = None, approval_id: str | None = None,
                  run_id: str | None = None, details: Mapping | None = None) -> int:
    """Async twin of record()."""
    from corpai.db import pg

    params = _params(event, actor, act_chain, action, resource, decision, reason, route, level,
                     approval_id, run_id, details)
    async with _sem():
        conn = await psycopg.AsyncConnection.connect(pg.app_dsn(), autocommit=True)
        async with conn:
            cur = await conn.execute(_APPEND_SQL, params)
            row = await cur.fetchone()
    return int(row[0])


def record_owner_job(job: str, details: Mapping | None = None) -> int:
    """Owner-role jobs (migrations, synth, ingestion, metric refresh, executors) announce themselves.

    Each one also anchors the chain head outside the database (corpai/audit/anchor.py): owner jobs
    are the code that holds the owner role anyway, and they run often enough that a cut tail or a
    re-hashed history is caught by verify_chain().
    """
    from corpai.audit.anchor import write_anchor

    if not isinstance(job, str) or not _JOB_RE.match(job):
        raise ValueError("job must be a short lower-case name like 'synth' or 'ingest'")
    seq = record("owner_job", actor=f"svc:{job}", details=details)
    write_anchor()
    return seq


def _is_auditor(principal: Principal) -> bool:
    return principal.active and principal.role == Role.SECURITY_AUDITOR


def _hexify(row: dict) -> dict:
    for k in ("prev_hash", "hash"):
        if row.get(k) is not None:
            row[k] = bytes(row[k]).hex()
    return row


async def read_log(principal: Principal, *, after_seq: int = 0, limit: int = 200) -> list[dict]:
    """Entries with seq > after_seq, oldest first. Security auditor only (checked here and in SQL)."""
    from corpai.db import pg

    if not _is_auditor(principal):
        raise PermissionError("only the security auditor may read the audit log")
    try:
        async with pg.principal_tx(principal) as conn:
            cur = await conn.execute("SELECT * FROM audit.read_log(%s::bigint, %s::int)",
                                     (int(after_seq), int(limit)))
            rows = await cur.fetchall()
    except psycopg.errors.InsufficientPrivilege as e:
        raise PermissionError("only the security auditor may read the audit log") from e
    return [_hexify(dict(r)) for r in rows]
