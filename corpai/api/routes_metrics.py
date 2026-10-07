"""The metric layer over HTTP (M3; design-review 二.8 "草稿 → CFO 签核 → 发布").

    GET  /api/metrics                          definitions, the periods and statuses the caller may see, and
                                               what the caller may do ({can: {sign, publish, refresh, drafts}});
                                               for the CFO in the demo also `refreshable`: the ended periods
                                               the refresh button may compute (a new month has no snapshot yet)
    GET  /api/metrics/board?period=P           every metric of P as the caller may see it (rows with status,
                                               value and provenance): the sign-off board
    GET  /api/metrics/{id}?period=P&dims=k=v   one value with its provenance (sql_sha256, signed_by, version...)
    POST /api/metrics/sign     {period, metric_ids}   head of finance only (checked here and again in SQL)
    POST /api/metrics/publish  {period, metric_ids}   head of finance only; signed rows only
    POST /api/metrics/refresh  {period}               head of finance, demo login only (see below)

What a caller sees is decided by the database (RLS on metrics.snapshots) and policy.decide on every
row, inside corpai.metrics: drafts and signed rows reach finance and the CEO only, published rows
their audience. Platform roles (admin, auditor) read no business metrics: the API refuses them, like
the MCP finance tools do (the data layer alone would show them published company-wide rows).

Refresh is the owner job svc:metrics. In production it runs on a schedule after the month-end
close, never on a click; the button exists for the demo, so it is offered only while demo login is
on, to the head of finance, and every attempt is audited (policy_decision, action metrics.refresh).
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from corpai import audit
from corpai.api import deps
from corpai.api.errors import bad_request, forbidden, not_found
from corpai.clock import today
from corpai.config import settings
from corpai.metrics import (
    format_value,
    get_breakdown,
    get_def,
    get_metric,
    list_metrics,
    load_metrics,
    parse_period,
    publish,
    refresh_snapshots,
    sign_off,
    table_key,
)
from corpai.metrics.periods import month_before, quarter_before
from corpai.types import Dept, Principal, Role

router = APIRouter()

_PLATFORM_ROLES = (Role.PLATFORM_ADMIN, Role.SECURITY_AUDITOR, Role.SERVICE)
_ONLY_CFO = "Only the head of finance signs off, publishes or refreshes metrics."
MAX_IDS = 40


class TransitionIn(BaseModel):
    period: str = Field(min_length=7, max_length=7)
    metric_ids: list[str] = Field(min_length=1, max_length=MAX_IDS)


class RefreshIn(BaseModel):
    period: str = Field(min_length=7, max_length=7)


def _finance_head(p: Principal) -> bool:
    # the same rule as corpai.metrics (and metrics.sign()/publish() in SQL): role, dept and group
    return p.active and p.role is Role.MANAGER and p.dept is Dept.FINANCE and "head:finance" in p.groups


def _reader(p: Principal) -> None:
    if not p.active or p.role in _PLATFORM_ROLES:
        raise forbidden("Platform roles do not read business metrics.")


def _sees_drafts(p: Principal) -> bool:
    return any(set(md.draft_acl) & set(p.groups) for md in load_metrics().values())


def _can(p: Principal) -> dict[str, bool]:
    head = _finance_head(p)
    return {"sign": head, "publish": head, "refresh": head and settings().demo_login, "drafts": _sees_drafts(p)}


_REFRESHABLE_MONTHS, _REFRESHABLE_QUARTERS = 12, 4


def _refreshable() -> list[str]:
    """Ended periods, newest first: the last months and quarters before the sim date. The refresh
    button offers these, so a just-closed month (or the very first period after `synth`) can be
    computed from the page; listing only periods that already have snapshots would hide them."""
    out: list[str] = []
    d = today()
    for _ in range(_REFRESHABLE_MONTHS):
        m = month_before(d)
        out.append(m.text)
        d = m.start
    d = today()
    for _ in range(_REFRESHABLE_QUARTERS):
        q = quarter_before(d)
        out.append(q.text)
        d = q.start
    return out


def _period(text: str) -> str:
    try:
        return parse_period(text).text
    except ValueError:
        raise bad_request("period must look like 2026-09 or 2026-Q3.") from None


def _value(mv: Any) -> dict:
    d = mv.to_dict()
    d["key"] = table_key(mv.metric_id, mv.dims)
    d["display_zh"], d["display_en"] = format_value(mv, "zh"), format_value(mv, "en")
    return d


@router.get("/api/metrics")
async def metrics_list(p: Principal = Depends(deps.principal)) -> dict:
    _reader(p)
    try:
        listed = await list_metrics(p)
    except PermissionError:
        raise forbidden("Your account may not read metrics.") from None
    periods = sorted({x["period"] for m in listed for x in m["periods"]}, reverse=True)
    can = _can(p)
    return {"metrics": listed, "periods": periods, "can": can, "refreshable": _refreshable() if can["refresh"] else []}


@router.get("/api/metrics/board")
async def metrics_board(period: str = "", p: Principal = Depends(deps.principal)) -> dict:
    _reader(p)
    period = _period(period)
    listed = {m["metric_id"]: m for m in await list_metrics(p)}
    rows = []
    for md in load_metrics().values():
        visible = await get_breakdown(p, md.id, period)  # every status the caller may see; RLS decides
        statuses = next((x["statuses"] for x in listed[md.id]["periods"] if x["period"] == period), [])
        rows.append({"metric_id": md.id, "name_zh": md.name_zh, "name_en": md.name_en, "unit": md.unit,
                     "level": int(md.level), "visible_to": md.public_dict().get("visible_to"),
                     "statuses": statuses, "rows": [_value(mv) for mv in visible]})
    return {"period": period, "metrics": rows, "can": _can(p)}


def _dims(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in [x for x in text.split(",") if x]:
        k, sep, v = part.partition("=")
        if not sep or not k or not v:
            raise bad_request("dims must look like dept=sales.")
        out[k] = v
    return out


@router.get("/api/metrics/{metric_id}")
async def metric_value(metric_id: str, period: str = "", dims: str = "", p: Principal = Depends(deps.principal)) -> dict:
    _reader(p)
    try:
        get_def(metric_id)
    except KeyError:
        raise not_found("No such metric.") from None
    period = _period(period)
    try:
        mv = await get_metric(p, metric_id, period, _dims(dims))
    except ValueError:
        raise bad_request("This metric has no such dimension.") from None
    return _value(mv)


async def _transition(p: Principal, body: TransitionIn, fn: Any) -> dict:
    period = _period(body.period)
    try:
        res = await fn(p, period, body.metric_ids)  # audited allow and deny (metric_signed / metric_published)
    except PermissionError:
        raise forbidden(_ONLY_CFO) from None
    except KeyError:
        raise bad_request("Unknown metric id; pick metrics from the list.") from None
    except ValueError:
        raise bad_request("Name at least one metric.") from None
    return res.to_dict()


@router.post("/api/metrics/sign")
async def metrics_sign(body: TransitionIn, p: Principal = Depends(deps.principal)) -> dict:
    return await _transition(p, body, sign_off)


@router.post("/api/metrics/publish")
async def metrics_publish(body: TransitionIn, p: Principal = Depends(deps.principal)) -> dict:
    return await _transition(p, body, publish)


@router.post("/api/metrics/refresh")
async def metrics_refresh(body: RefreshIn, p: Principal = Depends(deps.principal)) -> dict:
    period = _period(body.period)
    resource = f"fin.metric:{period}"
    if not (_finance_head(p) and settings().demo_login):
        reason = "only the head of finance, and only in the demo" if settings().demo_login else \
            "refresh runs as a scheduled job outside the demo"
        await audit.arecord("policy_decision", actor=p.user_id, action="metrics.refresh", resource=resource,
                            decision="deny", reason=reason)
        raise forbidden(_ONLY_CFO if settings().demo_login else "Metrics are refreshed by the scheduled job.")
    if parse_period(period).end > today():
        raise bad_request("That period has not ended yet; refresh a period that has ended.")
    await audit.arecord("policy_decision", actor=p.user_id, action="metrics.refresh", resource=resource,
                        decision="allow", reason="demo refresh by the head of finance")
    report = await anyio.to_thread.run_sync(lambda: refresh_snapshots(period))  # owner job, audits owner_job
    return report.to_dict()
