"""One section of the report, read with the REQUESTER's identity (design-review 二.7).

fetch_section() reads the section's metrics through corpai.metrics.get_breakdown (RLS plus
policy.decide on every row) with signed_only=True: a management report cites signed snapshots only
(design-review 二.8). It runs the section's issue detectors as the requester too. Nothing is skipped
silently: a metric with no visible row becomes an explicit "no permission or not published" entry,
a detector whose main table the requester cannot read stays in the section with status
no_permission.

Everything returned is plain JSON (strings, numbers, lists, dicts): it goes into the graph state and
from there into the requester's own checkpoint. No SQL text, no tokens, no person-level data (the
metric layer is aggregate-only and suppresses groups under five people before a value is stored).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from corpai.agents.report.domains import domain, metrics_of
from corpai.metrics import (
    NO_ACCESS,
    MetricValue,
    SignalResult,
    detect,
    format_value,
    get_breakdown,
    get_def,
    table_key,
)
from corpai.types import Level, Principal

# what a viewer needs to follow a number back to its source; nothing else of the provenance
_PROVENANCE = ("sql_sha256", "formula", "sources", "version", "computed_at", "signed_by", "signed_at",
               "published_at", "signal", "field")


def mv_to_state(mv: MetricValue) -> dict[str, Any]:
    d = mv.to_dict()
    d["provenance"] = {k: v for k, v in dict(mv.provenance).items() if k in _PROVENANCE}
    return d


def mv_from_state(d: Mapping[str, Any]) -> MetricValue:
    value = d.get("value")
    return MetricValue(metric_id=d["metric_id"], period=d["period"], dims=dict(d.get("dims") or {}),
                       value=None if value is None else Decimal(str(value)), unit=d["unit"],
                       status=d["status"], level=Level(int(d["level"])), suppressed=bool(d.get("suppressed")),
                       provenance=dict(d.get("provenance") or {}), reason=d.get("reason"),
                       name_zh=d.get("name_zh") or "", name_en=d.get("name_en") or "")


def signal_from_state(d: Mapping[str, Any]) -> SignalResult:
    return SignalResult(kind=d["kind"], status=d["status"], as_of=d["as_of"], findings=tuple(d.get("findings") or ()),
                        level=Level(int(d["level"])), sources=tuple(d.get("sources") or ()),
                        denied_sources=tuple(d.get("denied_sources") or ()), message=d.get("message") or "")


def _unavailable(metric_id: str, period: str) -> MetricValue:
    md = get_def(metric_id)
    return MetricValue(metric_id=metric_id, period=period, dims={}, value=None, unit=md.unit, status="unavailable",
                       level=Level.L1, reason=NO_ACCESS, name_zh=md.name_zh, name_en=md.name_en)


def section_status(metrics: list[dict], signals: list[dict]) -> str:
    """ok: everything visible; partial: some of it; no_permission: nothing at all."""
    seen = [m["status"] != "unavailable" for m in metrics] + [s["status"] == "ok" for s in signals]
    if seen and all(seen):
        return "ok"
    return "partial" if any(seen) else "no_permission"


# Detectors that compare figures over a period run on the REPORT's period, so an issue never contradicts
# a metric in the same section (a 2026-Q2 report must not list the 2026-Q3 overrun as its own). A month
# report checks that month. The others describe the state on the sim date (an overdue invoice, a
# contract expiring next month) and keep as_of.
_PERIOD_SIGNALS = frozenset({"dept_budget_overrun"})


def _signal_options(kind: str, period: str) -> dict[str, Any]:
    return {"period": period} if kind in _PERIOD_SIGNALS else {}


async def fetch_section(p: Principal, domain_id: str, *, period: str, as_of: date, run_id: str) -> dict[str, Any]:
    """The section as `p` may see it: {domain, title_zh, title_en, status, level, metrics, signals}."""
    d = domain(domain_id)
    metrics: list[dict] = []
    for metric_id in metrics_of(domain_id):
        rows = await get_breakdown(p, metric_id, period, signed_only=True) or [_unavailable(metric_id, period)]
        for mv in rows:
            entry = mv_to_state(mv)
            entry["key"] = table_key(mv.metric_id, mv.dims)
            entry["display_zh"], entry["display_en"] = format_value(mv, "zh"), format_value(mv, "en")
            metrics.append(entry)
    signals = [(await detect(p, kind, as_of=as_of, run_id=run_id, **_signal_options(kind, period))).to_dict()
               for kind in d.signals]
    visible = [m["level"] for m in metrics if m["status"] != "unavailable"] + \
        [s["level"] for s in signals if s["status"] == "ok"]
    return {"domain": d.id, "title_zh": d.title_zh, "title_en": d.title_en,
            "status": section_status(metrics, signals), "level": max(visible, default=int(Level.L1)),
            "metrics": metrics, "signals": signals}


def denied_items(section: Mapping[str, Any]) -> list[dict[str, str]]:
    """The section's explicit denials in the §18 tool-event shape [{id, reason}]."""
    out = [{"id": f"metric:{m['key']}", "reason": m.get("reason") or NO_ACCESS}
           for m in section["metrics"] if m["status"] == "unavailable"]
    out += [{"id": f"signal:{s['kind']}", "reason": "no permission"} for s in section["signals"]
            if s["status"] != "ok"]
    return out
