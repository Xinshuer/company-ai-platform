"""The finance metric layer (M3; design-review 二.7, 二.8; decisions D7).

Public API (import only these):
    definitions   load_metrics() -> {id: MetricDef}, get_def(id), MetricConfigError
    refresh       refresh_snapshots(period, *, metric_ids=None) -> RefreshReport      owner job svc:metrics
                  compute_all(period) -> {id: rows}                                    read-only, no write
    service       async get_metric(principal, metric_id, period, dims=None, *, signed_only=False) -> MetricValue
                  async get_breakdown(principal, metric_id, period, *, signed_only=False) -> [MetricValue]
                  async list_metrics(principal) -> [dict]
                  async sign_off(principal, period, metric_ids) -> SignResult           head of finance only
                  async publish(principal, period, metric_ids) -> SignResult            head of finance only
    numbers       render(template, table, *, lang) / verify_template(template, table) /
                  verify_numbers(text, table) -> [Problem] / table_key
    signals       async detect(principal, kind, *, as_of=None) / detect_all(principal) -> {kind: SignalResult}
    export        export_report(principal, *, title, body_md, run_id, domain, level, report_level) -> (bytes, filename)
                  aexport_report(...) (async) / verify_export(bytes)
    writer        async write_report_text(principal, *, run_id, table, signals, hwm, lang) -> ReportDraft
"""

from __future__ import annotations

from corpai.metrics.definitions import MetricConfigError, MetricDef, get_def, load_metrics
from corpai.metrics.export import aexport_report, export_report, verify_export
from corpai.metrics.model import NO_ACCESS, MetricValue, RefreshReport, SignResult, dims_key
from corpai.metrics.numbers import (
    Problem,
    RenderError,
    format_value,
    render,
    table_key,
    verify_numbers,
    verify_template,
)
from corpai.metrics.periods import Period, month_before, parse_period, quarter_before
from corpai.metrics.refresh import MetricSqlError, apply_min_group, compute_all, refresh_snapshots
from corpai.metrics.service import get_breakdown, get_metric, list_metrics, publish, sign_off
from corpai.metrics.signals import KINDS as SIGNAL_KINDS
from corpai.metrics.signals import SignalResult, detect, detect_all
from corpai.metrics.writer import ReportDraft, signal_table, write_report_text

__all__ = [
    "MetricConfigError", "MetricDef", "get_def", "load_metrics",
    "aexport_report", "export_report", "verify_export",
    "NO_ACCESS", "MetricValue", "RefreshReport", "SignResult", "dims_key",
    "Problem", "RenderError", "format_value", "render", "table_key", "verify_numbers", "verify_template",
    "Period", "month_before", "parse_period", "quarter_before",
    "MetricSqlError", "apply_min_group", "compute_all", "refresh_snapshots",
    "get_breakdown", "get_metric", "list_metrics", "publish", "sign_off",
    "SIGNAL_KINDS", "SignalResult", "detect", "detect_all",
    "ReportDraft", "signal_table", "write_report_text",
]
