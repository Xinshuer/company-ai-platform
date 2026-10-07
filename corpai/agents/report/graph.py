"""M3 management report graph (architecture §16, design-review 二.7, 二.8):

    plan        code reads the period and language from the input; hwm = max(input, user level)
    fetch_*     one node per domain (finance, sales, eng, hr, legal), run in parallel, each with the
                REQUESTER's identity: signed metric snapshots (RLS + policy) and the domain's issue
                detectors. What the requester may not see stays in the section as an explicit
                "no permission"; groups under five people arrive suppressed from the metric layer.
    write       the writer model (purpose report.write) gets the table as placeholders and writes
                prose only; corpai.metrics.write_report_text renders and verifies every number and
                falls back to code's template when the model's text fails. NO tools.
    finish      code adds the header and the list of what was not shown, renders and verifies the
                whole report again, and returns it with the table, the signals and the level.

No node reads untrusted content (no web, no uploads, no RAG), none writes or reaches outside: every
node holds sensitive_data only, so Rule of Two holds without an approval gate. The route is the
gateway's, from the hwm: a report that read L3/L4 rows is written by the local model.
Different requesters never share anything: each run reads with its own principal, and nothing is
cached between runs.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Callable, Mapping

from langgraph.graph import END, START, StateGraph

from corpai.agents.report import compose
from corpai.agents.report.domains import DOMAIN_IDS, export_domain
from corpai.agents.report.request import parse_request
from corpai.agents.report.sections import denied_items, fetch_section
from corpai.agents.runtime import BaseState, RunContext, Step, node, raise_hwm
from corpai.classify import classify_text
from corpai.clock import today
from corpai.metrics import write_report_text
from corpai.policy import decide
from corpai.types import Action, Label, Level, Resource

AGENT = "report"
APP = "report"


def _sections(old: dict | None, new: dict | None) -> dict:
    """Sections of the current run: a run's input sets None (reset), each fetch node adds its domain."""
    if new is None:
        return {}
    return {**(old or {}), **new}


class ReportState(BaseState, total=False):
    request: str
    period: str
    as_of: str
    lang: str
    refusal: str | None
    base_hwm: int
    sections: Annotated[dict, _sections]
    draft: dict | None
    output: dict | None


def initial_state(text: str) -> dict:
    """The per-run input. Run-scoped keys are reset; hwm and events carry over in a thread."""
    return {"request": text, "period": "", "as_of": "", "lang": "zh", "refusal": None, "sections": None,
            "draft": None, "output": None}


@node(caps={"sensitive_data"})
async def plan(state: ReportState, runtime) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    text = state.get("request") or ""
    c = classify_text(text)
    user_level = Level(max(1, min(4, int(ctx.user_level))))
    step.event({"t": "classify", "level": int(c.level), "findings": sorted({f.kind for f in c.findings})})
    step.raise_to(max(c.level, user_level), "input" if c.level >= user_level else "user_level")
    as_of = today()
    try:
        req = parse_request(text, as_of)
    except ValueError as e:
        lang = "zh" if any("㐀" <= ch <= "鿿" for ch in text) else "en"
        return step.update(refusal=str(e), lang=lang, as_of=as_of.isoformat())
    return step.update(period=req.period, lang=req.lang, as_of=as_of.isoformat(), refusal=None,
                       base_hwm=int(step.hwm))


def _route(state: ReportState) -> list[str] | str:
    return "refuse" if state.get("refusal") else [f"fetch_{d}" for d in DOMAIN_IDS]


@node(caps=set())
async def refuse(state: ReportState, runtime) -> dict:
    zh = state.get("lang") == "zh"
    msg = ("这个期间还没有结束，报告只能针对已经结束的期间（例如 2026-Q3 或 2026-09）。" if zh else
           "That period has not ended yet; ask for a period that has ended (for example 2026-Q3 or 2026-09).")
    return {"output": {"text": msg, "citations": [], "data": {"kind": "management_report", "refused": True}}}


def _fetch_node(domain_id: str) -> Callable[..., Any]:
    @node(caps={"sensitive_data"}, name=f"fetch_{domain_id}")
    async def fetch(state: ReportState, runtime) -> dict:
        ctx: RunContext = runtime.context
        step = Step(runtime, state)
        as_of = date.fromisoformat(state["as_of"])
        async with ctx.services.tracer.span(f"metrics:{domain_id}", kind="tool", run_id=ctx.run_id,
                                            principal=ctx.principal, level=step.hwm, input=state["period"]) as sp:
            sec = await fetch_section(ctx.principal, domain_id, period=state["period"], as_of=as_of,
                                      run_id=ctx.run_id)
            sp.set_level(sec["level"])
            sp.set_attr("status", sec["status"])
            sp.set_attr("metrics", len(sec["metrics"]))
            sp.set_attr("unavailable", sum(m["status"] == "unavailable" for m in sec["metrics"]))
            sp.set_attr("signals", [f"{s['kind']}:{s['status']}" for s in sec["signals"]])
        denied = denied_items(sec)
        # §18 tool-event shape, so the timeline shows each section's reads and denials explicitly
        step.event({"t": "tool", "domain": "metrics", "tool": domain_id,
                    "decision": "deny" if sec["status"] == "no_permission" else "allow",
                    "level": int(sec["level"]), "ok": sec["status"] != "no_permission", "denied": denied[:20]})
        # The five fetch nodes run in parallel from the same hwm, so each one only raises the channel
        # (reducer max: the data is in the session from this step on) and the write node reports the
        # rise once, with the section that caused it, instead of five overlapping hwm events.
        out = step.update(sections={domain_id: sec})
        if sec["status"] != "no_permission":
            out["sensitivity_hwm"] = max(int(out.get("sensitivity_hwm") or 0), int(sec["level"]))
        return out

    return fetch


def _route_reason(route: str | None, level: int) -> str:
    return "external_down_fallback" if route == "local" and level <= Level.L2 else "route_by_level"


@node(caps={"sensitive_data"})
async def write(state: ReportState, runtime) -> dict:
    """The writer: no tools, prose with placeholders only (corpai.metrics.write_report_text)."""
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    sections = state.get("sections") or {}
    table, signals = compose.tables(sections)
    lang = state.get("lang") or "zh"
    seen = [sec for sec in sections.values() if sec["status"] != "no_permission"]
    if seen:
        top = max(seen, key=lambda sec: int(sec["level"]))
        step.apply(raise_hwm({"sensitivity_hwm": int(state.get("base_hwm") or 1)}, int(top["level"]),
                             f"tool:metrics.{top['domain']}"))
    async with ctx.services.tracer.span("llm:report.write", kind="llm", run_id=ctx.run_id, principal=ctx.principal,
                                        level=step.hwm) as sp:
        draft = await write_report_text(ctx.principal, run_id=ctx.run_id, table=table, signals=signals,
                                        hwm=step.hwm, lang=lang, title=compose.title(state["period"], lang),
                                        agent=ctx.agent, gateway=ctx.services.gateway)
        sp.set_level(draft.level)
        sp.set_attr("route", draft.route)
        sp.set_attr("attempts", draft.attempts)
        sp.set_attr("used_fallback", draft.used_fallback)
    step.raise_to(draft.level, "model_input")
    step.event({"t": "route", "route": draft.route, "level": int(draft.level),
                "reason": _route_reason(draft.route, int(draft.level))})
    return step.update(draft={"template": draft.template, "used_fallback": draft.used_fallback,
                              "problems": sorted({p.kind for p in draft.problems}), "attempts": draft.attempts,
                              "route": draft.route, "level": int(draft.level)})


def _export_allowed(ctx: RunContext, level: int) -> bool:
    r = Resource(kind="report", id=ctx.run_id, label=Label(level=Level(level)),
                 attrs={"domain": export_domain(ctx.principal)})
    return decide(ctx.principal, Action.EXPORT, r).allow


@node(caps={"sensitive_data"})
async def finish(state: ReportState, runtime) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    sections = state.get("sections") or {}
    draft = state.get("draft") or {}
    lang, period, as_of = state.get("lang") or "zh", state["period"], state["as_of"]
    done = compose.compose(draft_template=draft.get("template") or "", sections=sections, order=list(DOMAIN_IDS),
                           period=period, as_of=as_of, lang=lang)
    table, signals = compose.tables(sections)
    full = compose.full_table(table, signals)
    level = int(step.hwm)
    ordered = [compose.section_view(sections[d]) for d in DOMAIN_IDS if d in sections]
    data = {
        "kind": "management_report", "run_id": ctx.run_id, "title": compose.title(period, lang), "period": period,
        "as_of": as_of, "lang": lang, "level": level, "route": draft.get("route"),
        "template": done["template"], "used_fallback": bool(draft.get("used_fallback") or done["used_fallback"]),
        "attempts": draft.get("attempts"), "problems": sorted(set(draft.get("problems") or []) | set(done["problems"])),
        "sections": ordered, "table": compose.table_view(full),
        "export": {"domain": export_domain(ctx.principal), "allowed": _export_allowed(ctx, level)},
    }
    return step.update(output={"text": done["text"], "citations": [], "data": data})


def build_graph() -> StateGraph:
    """The uncompiled graph; the router compiles it with the checkpointer."""
    g = StateGraph(ReportState, context_schema=RunContext)
    g.add_node("plan", plan)
    g.add_node("refuse", refuse)
    for d in DOMAIN_IDS:
        g.add_node(f"fetch_{d}", _fetch_node(d))
    g.add_node("write", write)
    g.add_node("finish", finish)
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", _route, {"refuse": "refuse", **{f"fetch_{d}": f"fetch_{d}" for d in DOMAIN_IDS}})
    g.add_edge("refuse", END)
    g.add_edge([f"fetch_{d}" for d in DOMAIN_IDS], "write")
    g.add_edge("write", "finish")
    g.add_edge("finish", END)
    return g


def output_of(values: Mapping[str, Any]) -> dict | None:
    out = values.get("output")
    return out if isinstance(out, dict) else None
