"""M3 management report (app `report`; architecture §16, design-review 二.7, 二.8, 七 M3).

Public API:
    build_graph(), initial_state(text)     the graph the router compiles (corpai.agents.router)
    DOMAINS, domain_of(metric_def), export_domain(principal)
    parse_request(text, today) -> ReportRequest(period, lang, explicit)
    export_run(principal, *, run_id, thread_id, checkpointer) -> ExportFile   watermarked file, audited
    find_report(principal, run_id, thread_id, checkpointer) -> (output, hwm)  the caller's own report run

Importing this package registers nothing global except what corpai.metrics registers itself (the
FakeLLM responder for purpose report.write).
"""

from __future__ import annotations

from corpai.agents.report.domains import DOMAINS, Domain, domain_of, export_domain, metrics_of
from corpai.agents.report.export import ExportFile, ExportNotFound, export_run, find_report
from corpai.agents.report.graph import AGENT, APP, ReportState, build_graph, initial_state
from corpai.agents.report.request import ReportRequest, parse_request

__all__ = [
    "AGENT", "APP", "DOMAINS", "Domain", "ExportFile", "ExportNotFound", "ReportRequest", "ReportState",
    "build_graph", "domain_of", "export_domain", "export_run", "find_report", "initial_state", "metrics_of",
    "parse_request",
]
