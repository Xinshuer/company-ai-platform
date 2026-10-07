"""The owner job that computes metric snapshots (architecture §4: metric refresh is one of the four jobs
allowed the owner connection; it writes an `owner_job` audit event and an audit anchor).

Compute and write are separate transactions: every metric is computed in one REPEATABLE READ, READ ONLY
transaction (one consistent snapshot of the ledger, and SQL from the YAML cannot write even by mistake),
then the rows are written in a second transaction under an advisory lock.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Iterable

import psycopg
from psycopg.types.json import Jsonb

from corpai import audit
from corpai.clock import now_utc
from corpai.db import pg
from corpai.metrics.definitions import MetricDef, load_metrics
from corpai.metrics.model import RefreshReport, dims_key
from corpai.metrics.periods import parse_period

log = logging.getLogger(__name__)

JOB = "metrics"
SERVICE_ID = "svc:metrics"
_LOCK_KEY = 70_070_070  # pg_advisory_xact_lock key of the refresh job


class MetricSqlError(RuntimeError):
    """A metric's SQL failed or returned columns other than its dims, value and n."""


def apply_min_group(rows: list[dict], min_group: int) -> list[dict]:
    """Mark rows derived from fewer than min_group people as suppressed and drop their value (D7).

    Each row: {"dims": {...}, "value": Decimal | None, "n": int | None}. When exactly one breakdown row
    is suppressed and a total is shown, the total minus the visible rows would give the hidden value
    away, so the smallest other breakdown row is suppressed as well (complementary suppression).
    """
    out = [dict(r, suppressed=False) for r in rows]
    if min_group <= 1:
        return out
    for r in out:
        if r.get("n") is None:
            raise MetricSqlError("a metric with min_group > 1 must return n for every row")
        if int(r["n"]) < min_group:
            r["suppressed"], r["value"] = True, None
    breakdown = [r for r in out if r["dims"]]
    total_shown = any(not r["dims"] and not r["suppressed"] for r in out)
    hidden = [r for r in breakdown if r["suppressed"]]
    visible = [r for r in breakdown if not r["suppressed"]]
    if total_shown and len(hidden) == 1 and visible:
        smallest = min(visible, key=lambda r: (int(r["n"]), sorted(r["dims"].items())))
        smallest["suppressed"], smallest["value"] = True, None
    return out


def _plan_relations(plan: Any) -> tuple[set[str], set[str]]:
    """(schema.relation names, schema-qualified function names) a JSON EXPLAIN plan touches."""
    rels: set[str] = set()
    funcs: set[str] = set()
    stack = [plan]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            if "Relation Name" in node:
                rels.add(f"{node.get('Schema', '?')}.{node['Relation Name']}")
            if "Function Name" in node:
                funcs.add(f"{node.get('Schema', '?')}.{node['Function Name']}")
            stack.extend(v for v in node.values() if isinstance(v, (list, dict)))
    return rels, funcs


def check_relations(conn: psycopg.Connection, md: MetricDef, params: dict) -> None:
    """The database's own answer to "which tables does this SQL read": EXPLAIN (VERBOSE) the statement
    and require every relation it scans to be one of the metric's declared `sources`. The loader's
    text check can be fooled by spellings Postgres accepts; the plan cannot. This SQL runs on the owner
    connection, where RLS does not apply, so this is what keeps a metric away from the synth schema (the
    evaluation answers), audit.*, ops.* and from undeclared people tables (D7 min_group)."""
    try:
        cur = conn.execute("EXPLAIN (VERBOSE, FORMAT JSON) " + md.sql, params)
    except psycopg.Error as e:
        raise MetricSqlError(f"metric {md.id}: sql failed ({type(e).__name__})") from e
    row = cur.fetchone()
    plan = row["QUERY PLAN"] if isinstance(row, dict) else row[0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    rels, funcs = _plan_relations(plan)
    extra = rels - set(md.sources)
    if extra:
        raise MetricSqlError(f"metric {md.id}: sql reads {sorted(extra)}, which are not its declared sources")
    foreign = {f for f in funcs if not f.startswith("pg_catalog.")}
    if foreign:
        raise MetricSqlError(f"metric {md.id}: sql scans functions {sorted(foreign)}")


def _compute(conn: psycopg.Connection, md: MetricDef, params: dict) -> list[dict]:
    check_relations(conn, md, params)
    try:
        cur = conn.execute(md.sql, params)
    except psycopg.Error as e:
        raise MetricSqlError(f"metric {md.id}: sql failed ({type(e).__name__})") from e
    cols = [c.name for c in (cur.description or [])]
    allowed = set(md.dims) | {"value", "n"}
    if "value" not in cols or set(cols) - allowed or set(md.dims) - set(cols):
        raise MetricSqlError(f"metric {md.id}: sql must return {sorted(md.dims)} + value (+ n), got {cols}")
    rows = []
    for r in cur.fetchall():
        dims = {d: str(r[d]) for d in md.dims if r[d] is not None}
        value = r["value"]
        rows.append({"dims": dims, "value": None if value is None else Decimal(value), "n": r.get("n")})
    keys = [dims_key(r["dims"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise MetricSqlError(f"metric {md.id}: sql returned the same dims twice")
    return apply_min_group(rows, md.min_group)


def _row_dept(md: MetricDef, dims: dict) -> str | None:
    return dims.get("dept") if "dept" in md.dims else md.dept


def compute_all(period: str, metric_ids: Iterable[str] | None = None) -> dict[str, list[dict]]:
    """Run each metric's SQL for a period without writing anything (owner connection, read only)."""
    p = parse_period(period)
    defs = load_metrics()
    if metric_ids is None:
        chosen = [md for md in defs.values() if p.grain in md.grain]
    else:
        chosen = []
        for mid in metric_ids:
            if mid not in defs:
                raise KeyError(f"unknown metric {mid!r}")
            if p.grain not in defs[mid].grain:
                raise ValueError(f"metric {mid} has no {p.grain} grain")
            chosen.append(defs[mid])
    params = p.params()
    out: dict[str, list[dict]] = {}
    with pg.connect_owner() as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        with conn.transaction():
            for md in chosen:
                out[md.id] = _compute(conn, md, params)
    return out


def refresh_snapshots(period: str, *, metric_ids: Iterable[str] | None = None) -> RefreshReport:
    """Compute every metric (or the given ones) for `period` and write draft snapshots.

    A draft is replaced in place. A signed or published snapshot is never touched: if the new result
    differs (value, suppression or SQL), a new draft version is added for the CFO to sign again.
    """
    p = parse_period(period)
    computed = compute_all(period, metric_ids)
    defs = load_metrics()
    now = now_utc()
    stats = {"rows": 0, "inserted": 0, "updated": 0, "unchanged": 0, "suppressed": 0, "new_versions": 0}
    with pg.connect_owner() as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            for mid, rows in computed.items():
                md = defs[mid]
                keep: list[str] = []
                for r in rows:
                    key = dims_key(r["dims"])
                    keep.append(key)
                    stats["rows"] += 1
                    stats["suppressed"] += int(r["suppressed"])
                    _write_row(conn, md, p.text, key, r, now, stats)
                # A draft whose group vanished (e.g. no open deals left in a stage) is stale: drop it.
                conn.execute(
                    "DELETE FROM metrics.snapshots WHERE metric_id = %s AND period = %s AND status = 'draft' "
                    "AND NOT (dims_key = ANY (%s))", (mid, p.text, keep))
    report = RefreshReport(period=p.text, metrics=len(computed), **stats)
    audit.record_owner_job(JOB, {"action": "refresh", "period": p.text, "metrics": report.metrics,
                                 "rows": report.rows, "suppressed": report.suppressed,
                                 "new_versions": report.new_versions})
    return report


def _write_row(conn: psycopg.Connection, md: MetricDef, period: str, key: str, r: dict, now: Any,
               stats: dict) -> None:
    dept = _row_dept(md, r["dims"])
    fields = {
        "value": r["value"], "suppressed": r["suppressed"], "unit": md.unit, "computed_at": now,
        "sql_sha256": md.sql_sha256, "sources": list(md.sources),
        "published_level": int(md.published_level), "published_acl": list(md.published_acl(dept)),
        "level": int(md.level), "dept": dept, "owner_id": SERVICE_ID, "acl_groups": list(md.draft_acl),
    }
    latest = conn.execute(
        "SELECT version, status, value, suppressed, sql_sha256, published_acl, published_level "
        "FROM metrics.snapshots WHERE metric_id = %s AND period = %s AND dims_key = %s "
        "ORDER BY version DESC LIMIT 1 FOR UPDATE", (md.id, period, key)).fetchone()
    if latest is None:
        _insert(conn, md.id, period, key, r["dims"], 1, fields)
        stats["inserted"] += 1
        return
    if latest["status"] == "draft":
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        conn.execute(f"UPDATE metrics.snapshots SET {sets} WHERE metric_id = %(m)s AND period = %(p)s "
                     f"AND dims_key = %(k)s AND version = %(v)s",
                     {**fields, "m": md.id, "p": period, "k": key, "v": latest["version"]})
        stats["updated"] += 1
        return
    same = (latest["value"] == r["value"] and latest["suppressed"] == r["suppressed"]
            and latest["sql_sha256"] == md.sql_sha256 and list(latest["published_acl"]) == fields["published_acl"]
            and latest["published_level"] == fields["published_level"])
    if same:
        stats["unchanged"] += 1
        return
    _insert(conn, md.id, period, key, r["dims"], int(latest["version"]) + 1, fields)
    stats["new_versions"] += 1


def _insert(conn: psycopg.Connection, metric_id: str, period: str, key: str, dims: dict, version: int,
            fields: dict) -> None:
    cols = ["metric_id", "period", "dims", "dims_key", "version", "status", *fields]
    values = {"metric_id": metric_id, "period": period, "dims": Jsonb(dims), "dims_key": key,
              "version": version, "status": "draft", **fields}
    conn.execute(f"INSERT INTO metrics.snapshots ({', '.join(cols)}) "
                 f"VALUES ({', '.join(f'%({c})s' for c in cols)})", values)
