"""Exporting a finished report as a watermarked file (design-review 二.7: 导出件加用户 id 和时间水印并留审计).

The exported text is never taken from the request: it is the report the run produced, read back
from the requester's OWN thread checkpoint (row-level security on the checkpoint tables means only
the thread's owner can read it). The export domain comes from the requester (export_domain) and the
watermark level is the run's hwm, so neither can be chosen by the caller. corpai.metrics.aexport_report
checks policy (EXPORT = READ of the report's domain) and writes the 'export' audit event, allow or deny.
A request refused before that check (no such run, not the caller's thread, the caller may not run reports)
is audited here with the same event, so probing for other people's report runs shows in the audit log.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from corpai import audit
from corpai.agents import _threads, router
from corpai.agents.report.domains import export_domain
from corpai.metrics import aexport_report, verify_export
from corpai.types import Level, Principal

APP = "report"
_RUN_RE = re.compile(r"^run_[0-9a-z]{20}$")
_THREAD_RE = re.compile(r"^thr_[0-9a-z]{20}$")
_MAX_HISTORY = 200  # checkpoints scanned for the run; a thread longer than that exports its recent runs only


class ExportNotFound(LookupError):
    """No such report run among the caller's own (someone else's looks the same)."""


@dataclass(frozen=True)
class ExportFile:
    content: bytes
    filename: str
    sha256: str
    level: int
    domain: str


@asynccontextmanager
async def _graph(route: Any, checkpointer: Any, p: Principal) -> AsyncIterator[Any]:
    """The compiled report graph with a saver acting as `p` (the API's CheckpointStore binds the
    connection to the principal, so RLS shows only p's own threads) or the given plain saver (tests)."""
    bind = getattr(checkpointer, "for_principal", None)
    if bind is None:
        yield router.compiled_graph(route, checkpointer)
        return
    async with bind(p, run_id=None) as saver:
        yield router.compiled_graph(route, None).copy(update={"checkpointer": saver})


async def find_report(p: Principal, run_id: str, thread_id: str, checkpointer: Any) -> tuple[dict, int]:
    """(output, hwm) of report run `run_id` in the caller's own thread `thread_id`."""
    # the role check comes first: someone who may not run reports gets the same 403 whatever ids they send
    route = router.resolve(p, APP)  # StartDenied for someone who may not (or no longer) run reports
    if not isinstance(run_id, str) or not _RUN_RE.match(run_id) or not isinstance(thread_id, str) \
            or not _THREAD_RE.match(thread_id):
        raise ExportNotFound("no such report")
    row = await _threads.get_own(p, thread_id)
    if row is None or row["app"] != APP:
        raise ExportNotFound("no such report")
    async with _graph(route, checkpointer, p) as graph:
        n = 0
        async for snap in graph.aget_state_history({"configurable": {"thread_id": thread_id}}):
            n += 1
            values = snap.values or {}
            out = values.get("output")
            data = out.get("data") if isinstance(out, dict) else None
            if isinstance(data, dict) and data.get("run_id") == run_id and data.get("kind") == "management_report":
                return out, int(values.get("sensitivity_hwm") or Level.L4)
            if n >= _MAX_HISTORY:
                break
    raise ExportNotFound("no such report")


def _rule_of(e: Exception) -> str:
    """The policy rule id StartDenied carries at the end of its message, e.g. '(agent-start-denied)'."""
    m = re.search(r"\(([a-z0-9-]+)\)\s*$", str(e))
    return m.group(1) if m else type(e).__name__


async def _audit_refusal(p: Principal, run_id: Any, thread_id: Any, reason: str, rule: str) -> None:
    """One 'export' deny row for a refusal that never reached the policy check. The ids come from the
    request, so only well-formed ones are written (a free-form path segment could carry anything)."""
    ok_run = isinstance(run_id, str) and bool(_RUN_RE.match(run_id))
    ok_thread = isinstance(thread_id, str) and bool(_THREAD_RE.match(thread_id))
    await audit.arecord("export", actor=p.user_id, action="export_report",
                        resource=f"report:{run_id if ok_run else 'invalid'}", decision="deny", reason=reason,
                        run_id=run_id if ok_run else None,
                        details={"rule": rule, "thread_id": thread_id if ok_thread else None})


async def export_run(p: Principal, *, run_id: str, thread_id: str, checkpointer: Any) -> ExportFile:
    """The watermarked Markdown of a finished report run. PermissionError when policy refuses the export
    (audited by aexport_report), ExportNotFound when the run is not the caller's report and StartDenied
    when the caller may not run reports (both audited here)."""
    try:
        out, hwm = await find_report(p, run_id, thread_id, checkpointer)
    except ExportNotFound:
        # someone else's run and a missing one are one reason: the audit row must not tell them apart either
        await _audit_refusal(p, run_id, thread_id, "not_found_or_not_own", "report-not-own")
        raise
    except router.RouterError as e:
        await _audit_refusal(p, run_id, thread_id, "may_not_run_reports", _rule_of(e))
        raise
    data = out["data"]
    level = max(int(hwm), int(data.get("level") or Level.L4))
    domain = export_domain(p)
    title = str(data.get("title") or "Management report")
    body = str(out.get("text") or "")
    # the export writes the title itself, between the watermark and the body: drop the report's own copy
    if body.startswith(f"# {title}\n"):
        body = body[len(title) + 3:].lstrip("\n")
    content, filename = await aexport_report(p, title=title, body_md=body, run_id=run_id, domain=domain,
                                             level=level, report_level=level)
    check = verify_export(content)
    head = content.decode("utf-8").splitlines()[0]
    m = re.search(r"\| (L[1-4]) ", head)
    return ExportFile(content=content, filename=filename, sha256=check.sha256 or "",
                      level=int(m.group(1)[1]) if m else level, domain=domain)
