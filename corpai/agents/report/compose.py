"""Assembling the final report from the writer's draft: code adds the frame, never a figure of its own.

    final template = header (period, reporting date)
                   + the writer's template (model prose with {metric:...} placeholders, or code's
                     fallback template when the model's text failed verification)
                   + an appendix written by code that lists everything the requester may not see

The appendix makes "no permission" explicit even when a model leaves it out of its prose
(ENGINEERING.md rule 6). The whole template is rendered and checked again with verify_template and
verify_numbers over the full table; if that ever fails, the report falls back to code's template, so a
number that is not a rendered table value never reaches the reader or an export.
"""

from __future__ import annotations

from typing import Any, Mapping

from corpai.agents.report.sections import mv_from_state, signal_from_state
from corpai.metrics import (
    MetricValue,
    SignalResult,
    format_value,
    render,
    signal_table,
    verify_numbers,
    verify_template,
)
from corpai.metrics.writer import template_text

TITLE = {"zh": "管理层报告", "en": "Management report"}
_SIGNAL_TITLES = {
    "overdue_receivable": ("大客户回款逾期", "Overdue receivable"),
    "contract_expiring": ("合同下月到期", "Contract expiring next month"),
    "project_testing_delayed": ("项目测试阶段延期", "Project stuck in testing"),
    "hiring_over_budget": ("招聘超预算", "Hiring over budget"),
    "dept_budget_overrun": ("部门费用超预算", "Department over budget"),
    "missing_dpa_clause": ("合同缺少数据处理条款", "Contract missing data processing terms"),
}


def title(period: str, lang: str) -> str:
    return f"{TITLE['zh' if lang == 'zh' else 'en']} {period}"


def tables(sections: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, MetricValue], dict[str, SignalResult]]:
    """(metric table by placeholder key, signals by kind) from the sections in the state."""
    table: dict[str, MetricValue] = {}
    signals: dict[str, SignalResult] = {}
    for sec in sections.values():
        for m in sec["metrics"]:
            table[m["key"]] = mv_from_state(m)
        for s in sec["signals"]:
            signals[s["kind"]] = signal_from_state(s)
    return table, signals


def full_table(table: Mapping[str, MetricValue], signals: Mapping[str, SignalResult]) -> dict[str, MetricValue]:
    """The table the writer rendered: metrics plus the numeric evidence of the signals."""
    return {**dict(table), **signal_table(signals)}


def header(period: str, as_of: str, lang: str) -> str:
    if lang == "zh":
        return (f"# {title(period, 'zh')}\n\n"
                f"> 数字来自指标层，只引用已签核的快照（期间 {period}）；问题检测截至 {as_of}。"
                f"本报告按你的权限生成，看不到的内容列在文末。\n\n")
    return (f"# {title(period, 'en')}\n\n"
            f"> Figures come from the metric layer and cite signed snapshots only (period {period}); issues are "
            f"checked as of {as_of}. This report shows what you may see; what you may not is listed at the end.\n\n")


def appendix(sections: Mapping[str, Mapping[str, Any]], order: list[str], lang: str) -> str:
    """Code's list of what the requester may not see, with figures as placeholders (they render as the
    explicit '[no permission or not published]' / '[not shown: group too small]' notices)."""
    zh = lang == "zh"
    lines: list[str] = []
    for dom in order:
        sec = sections.get(dom)
        if sec is None:
            continue
        name = sec["title_zh"] if zh else sec["title_en"]
        if sec["status"] == "no_permission":
            lines.append(f"- {name}：整个部分无权限查看或尚未发布" if zh else
                         f"- {name}: no permission for this whole section, or nothing published yet")
            continue
        hidden = [m for m in sec["metrics"] if m["status"] == "unavailable"]
        small = [m for m in sec["metrics"] if m["status"] != "unavailable" and m.get("suppressed")]
        for m in hidden + small:
            label = (m.get("name_zh") if zh else m.get("name_en")) or m["metric_id"]
            dims = ", ".join(f"{k}={v}" for k, v in sorted((m.get("dims") or {}).items()))
            lines.append(f"- {name} · {label}{f' ({dims})' if dims else ''}：{{metric:{m['key']}}}" if zh else
                         f"- {name} · {label}{f' ({dims})' if dims else ''}: {{metric:{m['key']}}}")
        for s in sec["signals"]:
            if s["status"] != "ok":
                t_zh, t_en = _SIGNAL_TITLES.get(s["kind"], (s["kind"], s["kind"]))
                lines.append(f"- {name} · {t_zh}：无权限查看" if zh else f"- {name} · {t_en}: no permission to check")
    head = "## 无权限或未展示的内容" if zh else "## Not shown to you"
    if not lines:
        lines = ["- 无：所有部分都可以查看。" if zh else "- Nothing: every section is visible to you."]
    return "\n\n" + head + "\n\n" + "\n".join(lines) + "\n"


def compose(*, draft_template: str, sections: Mapping[str, Mapping[str, Any]], order: list[str], period: str,
            as_of: str, lang: str) -> dict[str, Any]:
    """{template, text, problems, used_fallback}: the final report, every figure rendered by code and
    verified over the full table."""
    table, signals = tables(sections)
    full = full_table(table, signals)
    frame_head, frame_tail = header(period, as_of, lang), appendix(sections, order, lang)
    body = draft_template.strip("\n")
    template = frame_head + body + "\n" + frame_tail
    problems = verify_template(template, full)
    text = render(template, full, lang=lang) if not problems else ""
    if not problems:
        problems = verify_numbers(text, full)
    used_fallback = False
    if problems:
        # Defence in depth: the writer already verified its own text; if the assembled report still
        # fails, code's template replaces the prose (it cites every figure by placeholder only).
        template = frame_head + template_text(full, signals, lang).strip("\n") + "\n" + frame_tail
        text = render(template, full, lang=lang)
        used_fallback = True
    return {"template": template, "text": text, "problems": [p.kind for p in problems],
            "used_fallback": used_fallback}


def section_view(sec: Mapping[str, Any]) -> dict[str, Any]:
    """A section as the run returns it: the figures by placeholder key (their values and provenance live
    once, in the table), the signals with their evidence."""
    keep = ("key", "metric_id", "dims", "status", "suppressed", "level")
    return {**{k: v for k, v in sec.items() if k != "metrics"},
            "metrics": [{k: m.get(k) for k in keep} for m in sec["metrics"]]}


def table_from_view(view: Mapping[str, Mapping[str, Any]]) -> dict[str, MetricValue]:
    """The MetricValues behind a run's table (the inverse of table_view), for checks and re-rendering."""
    return {k: mv_from_state(v) for k, v in view.items()}


def table_view(full: Mapping[str, MetricValue]) -> dict[str, dict[str, Any]]:
    """The table as the UI shows it next to the report: each placeholder key with both displays and the
    provenance a reader needs to follow the number back (metric id, SQL hash, signed by, sources)."""
    out: dict[str, dict[str, Any]] = {}
    for key, mv in full.items():
        d = mv.to_dict()
        d["key"] = key
        d["display_zh"], d["display_en"] = format_value(mv, "zh"), format_value(mv, "en")
        out[key] = d
    return out
