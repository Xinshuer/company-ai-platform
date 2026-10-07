"""Request-path API of the metric layer: read through RLS, sign and publish through SECURITY DEFINER
functions. Every call runs inside pg.principal_tx(principal), so the database decides what is visible.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping

import psycopg

from corpai import audit
from corpai.db import pg
from corpai.metrics.definitions import MetricDef, get_def, load_metrics
from corpai.metrics.model import MetricValue, SignResult, dims_key, unavailable
from corpai.metrics.periods import parse_period
from corpai.policy import decide
from corpai.types import Action, Dept, Label, Level, Principal, Resource, Role, Trust

_COLS = ("metric_id, period, dims, dims_key, version, value, suppressed, unit, status, computed_at, sql_sha256, "
         "sources, signed_by, signed_at, published_by, published_at, level, dept, acl_groups, trust")


def _label(row: Mapping[str, Any]) -> Label:
    dept = row.get("dept")
    return Label(level=Level(int(row["level"])), dept=Dept(dept) if dept else None,
                 acl_groups=frozenset(row["acl_groups"]), trust=Trust(row.get("trust") or "reviewed"),
                 sources=(f"fin.metric:{row['metric_id']}:{row['period']}",))


def _visible(principal: Principal, row: Mapping[str, Any]) -> bool:
    """RLS already filtered the row; decide() must agree (architecture §5.1: reads agree by construction).
    A disagreement means a bug, and the stricter answer wins."""
    r = Resource(kind="fin.metric", id=f"{row['metric_id']}:{row['period']}:{row['dims_key']}",
                 label=_label(row), attrs={"status": row["status"]})
    return decide(principal, Action.READ, r).allow


def _to_value(md: MetricDef, row: Mapping[str, Any]) -> MetricValue:
    def iso(v: Any) -> str | None:
        return v.isoformat() if v is not None else None

    value = row["value"]
    return MetricValue(
        metric_id=row["metric_id"], period=row["period"], dims=dict(row["dims"] or {}),
        value=None if value is None else Decimal(value), unit=row["unit"], status=row["status"],
        level=Level(int(row["level"])), suppressed=bool(row["suppressed"]),
        provenance={"sql_sha256": row["sql_sha256"], "computed_at": iso(row["computed_at"]),
                    "signed_by": row["signed_by"], "signed_at": iso(row["signed_at"]),
                    "published_at": iso(row["published_at"]), "version": int(row["version"]),
                    "sources": list(row["sources"] or []), "formula": md.formula},
        reason="group below the minimum size; not shown" if row["suppressed"] else None,
        name_zh=md.name_zh, name_en=md.name_en)


def _status_filter(signed_only: bool) -> str:
    # Management reports cite signed snapshots only (design-review 二.8).
    return " AND status IN ('signed', 'published')" if signed_only else ""


async def get_metric(principal: Principal, metric_id: str, period: str, dims: Mapping[str, str] | None = None,
                     *, signed_only: bool = False) -> MetricValue:
    """The newest version of one metric row the principal may read.

    Nothing visible (no permission, not published yet, never computed) → status "unavailable" with the
    reason "no permission or not published". Never a silent zero.
    """
    md = get_def(metric_id)
    p = parse_period(period)
    if dims and not set(dims) <= set(md.dims):
        raise ValueError(f"metric {metric_id} has no dimension {sorted(set(dims) - set(md.dims))}")
    key = dims_key(dims)
    async with pg.principal_tx(principal) as conn:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM metrics.snapshots WHERE metric_id = %s AND period = %s AND dims_key = %s"
            f"{_status_filter(signed_only)} ORDER BY version DESC", (metric_id, p.text, key))
        rows = await cur.fetchall()
    for row in rows:
        if _visible(principal, row):
            return _to_value(md, row)
    return unavailable(metric_id, p.text, dims, md.unit, name_zh=md.name_zh, name_en=md.name_en)


async def get_breakdown(principal: Principal, metric_id: str, period: str, *,
                        signed_only: bool = False) -> list[MetricValue]:
    """Every visible row of a metric for a period (the newest visible version of each dims group),
    total first. Empty list = nothing visible; callers report that as "no permission or not published"."""
    md = get_def(metric_id)
    p = parse_period(period)
    async with pg.principal_tx(principal) as conn:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM metrics.snapshots WHERE metric_id = %s AND period = %s"
            f"{_status_filter(signed_only)} ORDER BY dims_key, version DESC", (metric_id, p.text))
        rows = await cur.fetchall()
    seen: dict[str, MetricValue] = {}
    for row in rows:
        if row["dims_key"] not in seen and _visible(principal, row):
            seen[row["dims_key"]] = _to_value(md, row)
    return [seen[k] for k in sorted(seen, key=lambda k: (k != "", k))]


async def list_metrics(principal: Principal) -> list[dict[str, Any]]:
    """Every metric definition plus the periods and statuses this principal can see.

    Definitions are not secret (they live in the repo); values are, and appear only through get_metric.
    """
    if not principal.active:
        raise PermissionError("the account is deactivated")
    async with pg.principal_tx(principal) as conn:
        cur = await conn.execute(
            "SELECT metric_id, period, dims_key, version, status, level, acl_groups, dept, trust "
            "FROM metrics.snapshots ORDER BY metric_id, period, dims_key, version DESC")
        rows = await cur.fetchall()
    visible: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        if _visible(principal, row):
            visible.setdefault(row["metric_id"], {}).setdefault(row["period"], set()).add(row["status"])
    out = []
    for md in load_metrics().values():
        periods = visible.get(md.id, {})
        out.append({**md.public_dict(),
                    "periods": [{"period": k, "statuses": sorted(v)} for k, v in sorted(periods.items())],
                    "access": "visible" if periods else "no permission or not published"})
    return out


# ---- sign-off and publication (head of finance only) -------------------------------------------

def _is_finance_head(p: Principal) -> bool:
    return p.active and p.role == Role.MANAGER and p.dept == Dept.FINANCE and "head:finance" in p.groups


def _check_ids(metric_ids: Iterable[str]) -> list[str]:
    ids = list(dict.fromkeys(metric_ids))
    if not ids:
        raise ValueError("name at least one metric")
    defs = load_metrics()
    unknown = [m for m in ids if m not in defs]
    if unknown:
        raise KeyError(f"unknown metrics {unknown}")
    return ids


# Sign-off and publication are human acts with their own audit events (issues-log D-13); the
# refusals use the same event with decision deny, so one query shows every attempt.
_EVENT = {"metrics.sign": "metric_signed", "metrics.publish": "metric_published"}


async def _transition(principal: Principal, period: str, metric_ids: Iterable[str], *, fn: str,
                      action: str) -> SignResult:
    event = _EVENT[action]
    p = parse_period(period)
    ids = _check_ids(metric_ids)
    resource = f"fin.metric:{p.text}"
    if not _is_finance_head(principal):
        # Audited like every refusal; the SQL function would refuse too (role, dept, group, clearance).
        await audit.arecord(event, actor=principal.user_id, action=action, resource=resource,
                            decision="deny", reason="only the head of finance may sign off or publish metrics",
                            details={"metrics": ids})
        raise PermissionError("only the head of finance may sign off or publish metrics")
    try:
        async with pg.principal_tx(principal) as conn:
            cur = await conn.execute(f"SELECT o_metric_id, o_dims_key, o_version FROM metrics.{fn}(%s, %s)",
                                     (p.text, ids))
            rows = await cur.fetchall()
    except psycopg.errors.InsufficientPrivilege as e:
        await audit.arecord(event, actor=principal.user_id, action=action, resource=resource,
                            decision="deny", reason="refused by the database", details={"metrics": ids})
        raise PermissionError("only the head of finance may sign off or publish metrics") from e
    changed = tuple(sorted({r["o_metric_id"] for r in rows}))
    result = SignResult(period=p.text, changed=changed, rows=len(rows),
                        skipped=tuple(m for m in ids if m not in changed))
    await audit.arecord(event, actor=principal.user_id, action=action, resource=resource,
                        decision="allow", reason=f"{action} by the head of finance",
                        details={"metrics": list(changed), "rows": result.rows, "skipped": list(result.skipped)})
    return result


async def sign_off(principal: Principal, period: str, metric_ids: Iterable[str]) -> SignResult:
    """Move the newest draft of each metric to `signed` (CFO only). Audited allow and deny."""
    return await _transition(principal, period, metric_ids, fn="sign", action="metrics.sign")


async def publish(principal: Principal, period: str, metric_ids: Iterable[str]) -> SignResult:
    """Publish the newest signed version of each metric to its audience (CFO only, after sign-off).
    Drafts are skipped and listed in `skipped`."""
    return await _transition(principal, period, metric_ids, fn="publish", action="metrics.publish")
