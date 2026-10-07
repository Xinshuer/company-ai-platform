"""The report writer: the model writes prose, code writes every number (design-review 二.8).

write_report_text() gives the model the metric table as placeholders, asks for prose that uses only
those placeholders, renders them with numbers.render() and checks the result with verify_numbers().
If the model invents or alters a figure, it gets one retry with the problem list; after that a
deterministic template written by code replaces its text, so a wrong number never reaches the reader.

The node that calls this must have no tools (Rule of Two): its input is internal data (sensitive) and its
output is shown to a person; it holds no write or outbound capability. The gateway route follows the
hwm computed here from the table and signals, never from the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from corpai.gateway import ChatRequest, fake_llm, get_gateway
from corpai.metrics.model import MetricValue
from corpai.metrics.numbers import Problem, RenderError, format_value, render, verify_numbers, verify_template
from corpai.metrics.signals import SignalResult
from corpai.types import Level, Principal

PURPOSE = "report.write"
_BLOCK_START, _BLOCK_END = "PLACEHOLDERS (key | name | value):", "END PLACEHOLDERS"
_SIGNAL_UNITS = (("_eur", "EUR"), ("_pct", "pct"), ("days", "days"), ("_runs", "count"))
_SIGNAL_TITLES = {
    "overdue_receivable": ("大客户回款逾期", "Overdue receivable"),
    "contract_expiring": ("合同下月到期", "Contract expiring next month"),
    "project_testing_delayed": ("项目测试阶段延期", "Project stuck in testing"),
    "hiring_over_budget": ("招聘超预算", "Hiring over budget"),
    "dept_budget_overrun": ("部门费用超预算", "Department over budget"),
    "missing_dpa_clause": ("合同缺少数据处理条款", "Contract missing data processing terms"),
}


@dataclass(frozen=True)
class ReportDraft:
    text: str                     # final text, every number rendered by code
    template: str                 # the placeholder text that was rendered
    problems: tuple[Problem, ...]  # problems of the model text that was rejected (empty when accepted)
    used_fallback: bool
    route: str | None
    level: Level                  # the hwm the model call ran at
    attempts: int


def _unit_for(field: str) -> str | None:
    """Unit of a numeric signal field, from its name (signals name every figure with its unit)."""
    for marker, unit in _SIGNAL_UNITS:
        if field.endswith(marker) or (marker == "days" and "days" in field.split("_")):
            return unit
    return None


def signal_table(signals: Mapping[str, SignalResult]) -> dict[str, MetricValue]:
    """Numeric evidence of the signals as table entries ('signal.<kind>.<ref>.<field>'), so prose can cite
    a days-overdue figure through a placeholder instead of copying the digits."""
    out: dict[str, MetricValue] = {}
    for kind, s in signals.items():
        if s.status != "ok":
            continue
        for f in s.findings:
            for name, v in f.items():
                unit = _unit_for(name)
                if unit is None or isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
                    continue
                key = f"signal.{kind}.{f['ref']}.{name}"
                out[key] = MetricValue(metric_id=f"signal.{kind}", period=s.as_of[:7], dims={"ref": f["ref"]},
                                       value=Decimal(str(v)), unit=unit, status="computed", level=s.level,
                                       provenance={"signal": kind, "field": name, "sources": list(s.sources)},
                                       name_zh=name, name_en=name)
    return out


def report_level(table: Mapping[str, MetricValue], signals: Mapping[str, SignalResult], hwm: Level | int) -> Level:
    """The writer's hwm: the highest of the caller's hwm and every value and signal it is shown."""
    levels = [int(hwm)] + [int(mv.level) for mv in table.values()] + [int(s.level) for s in signals.values()]
    return Level(max(levels))


def _placeholder_block(table: Mapping[str, MetricValue], lang: str) -> str:
    lines = [_BLOCK_START]
    for key in sorted(table):
        mv = table[key]
        name = (mv.name_zh if lang == "zh" else mv.name_en) or mv.metric_id
        lines.append(f"{key} | {name} | {format_value(mv, lang)}")
    lines.append(_BLOCK_END)
    return "\n".join(lines)


def _signal_block(signals: Mapping[str, SignalResult], lang: str) -> str:
    lines = ["ISSUES:"]
    for kind, s in signals.items():
        zh, en = _SIGNAL_TITLES.get(kind, (kind, kind))
        title = zh if lang == "zh" else en
        if s.status != "ok":
            lines.append(f"- {kind} | {title} | no permission")
        elif not s.findings:
            lines.append(f"- {kind} | {title} | none found")
        else:
            for f in s.findings:
                lines.append(f"- {kind} | {title} | ref {f['ref']}")
    return "\n".join(lines)


def _messages(table: Mapping[str, MetricValue], signals: Mapping[str, SignalResult], lang: str,
              title: str, feedback: list[Problem] | None) -> list[dict]:
    language = "Chinese (简体中文)" if lang == "zh" else "English"
    system = (
        "You write the narrative of an internal management report in " + language + ". "
        "Rules: (1) never write a digit for a figure: every amount, percentage, count or number of days must "
        "be a placeholder exactly as listed, written {metric:<key>}; (2) use only the facts listed; (3) mention "
        "every issue, and say plainly when a figure or issue is not available to the reader; (4) no tables, "
        "no invented metrics, no advice beyond the facts. Dates, years and ids like A-0001 may be written.")
    user = f"Report: {title}\n\n{_placeholder_block(table, lang)}\n\n{_signal_block(signals, lang)}"
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if feedback:
        listed = "; ".join(sorted({f"{p.kind} '{p.token}'" for p in feedback}))[:800]
        msgs.append({"role": "user", "content": "Your previous text was rejected: " + listed +
                     ". Rewrite it with placeholders only."})
    return msgs


def template_text(table: Mapping[str, MetricValue], signals: Mapping[str, SignalResult], lang: str = "en") -> str:
    """The deterministic text code writes when the model's text fails verification (and the fake model's
    answer): one line per figure and one per issue, figures as placeholders."""
    zh = lang == "zh"
    out = ["## 关键指标" if zh else "## Key figures", ""]
    for key in sorted(k for k in table if not k.startswith("signal.")):
        mv = table[key]
        dims = ", ".join(f"{k}={v}" for k, v in sorted(mv.dims.items()))
        name = (mv.name_zh if zh else mv.name_en) or mv.metric_id
        out.append(f"- {name}{f' ({dims})' if dims else ''}: {{metric:{key}}}")
    out += ["", "## 发现的问题" if zh else "## Issues found", ""]
    for kind, s in signals.items():
        t_zh, t_en = _SIGNAL_TITLES.get(kind, (kind, kind))
        title = t_zh if zh else t_en
        if s.status != "ok":
            out.append(f"- {title}: " + ("无权限查看" if zh else "no permission to check"))
            continue
        if not s.findings:
            out.append(f"- {title}: " + ("未发现" if zh else "none found"))
            continue
        for f in s.findings:
            facts = [f"{{metric:signal.{kind}.{f['ref']}.{name}}}" for name, v in f.items()
                     if _unit_for(name) and not isinstance(v, bool) and isinstance(v, (int, float, Decimal))]
            out.append(f"- {title}: {f['ref']}" + (f" ({', '.join(facts)})" if facts else ""))
    return "\n".join(out) + "\n"


def _parse_block(text: str) -> list[str]:
    m = re.search(re.escape(_BLOCK_START) + r"\n(.*?)\n" + re.escape(_BLOCK_END), text, re.S)
    if not m:
        return []
    return [line.split(" | ", 1)[0] for line in m.group(1).splitlines() if " | " in line]


def fake_report_writer(messages: list[dict], *, route: str, json_mode: bool) -> str:
    """Deterministic FakeLLM responder for 'report.write': a short narrative that cites every listed
    placeholder and never writes a figure itself."""
    user = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    keys = _parse_block(user)
    issues = [line[2:].split(" | ") for line in user.splitlines() if line.startswith("- ") and " | " in line]
    lines = [f"[fake/{route}] Management summary.", ""]
    lines += [f"- {k}: {{metric:{k}}}" for k in keys]
    for parts in issues:
        if len(parts) >= 3:
            lines.append(f"- issue {parts[0]}: {parts[2]}")
    return "\n".join(lines) + "\n"


def register_fake() -> None:
    fake_llm().register(PURPOSE, fake_report_writer)


register_fake()


async def write_report_text(principal: Principal, *, run_id: str, table: Mapping[str, MetricValue],
                            signals: Mapping[str, SignalResult] | None = None, hwm: Level | int = Level.L1,
                            lang: str = "zh", title: str = "Management report", agent: str = "report",
                            max_attempts: int = 2) -> ReportDraft:
    """Ask the model for prose with placeholders; render and verify; fall back to code's template.

    `table` maps placeholder keys to MetricValues (signal evidence is added automatically). The level
    sent to the gateway is computed here (report_level) and can only be higher than `hwm`.
    """
    signals = dict(signals or {})
    full = {**dict(table), **signal_table(signals)}
    level = report_level(full, signals, hwm)
    feedback: list[Problem] | None = None
    route: str | None = None
    attempts = 0
    for _ in range(max(1, max_attempts)):
        attempts += 1
        result = await get_gateway().chat(ChatRequest(
            messages=_messages(full, signals, lang, title, feedback), principal=principal, hwm=level,
            purpose=PURPOSE, run_id=run_id, agent=agent, max_tokens=1500, temperature=0.2))
        route = str(result.route)
        template = result.text
        try:
            text = render(template, full, lang=lang)
        except RenderError as e:
            feedback = [Problem("unknown_metric", str(e), 0, 0, "placeholder not in the table")]
            continue
        # The template first: the model may not write any figure itself, so a swapped, invented,
        # scaled or sign-flipped number is caught even when its digits happen to be in the table.
        problems = verify_template(template, full) + verify_numbers(text, full)
        if not problems:
            return ReportDraft(text=text, template=template, problems=(), used_fallback=False, route=route,
                               level=level, attempts=attempts)
        feedback = problems
    template = template_text(full, signals, lang)
    text = render(template, full, lang=lang)
    return ReportDraft(text=text, template=template, problems=tuple(feedback or ()), used_fallback=True,
                       route=route, level=level, attempts=attempts)


def table_json(table: Mapping[str, MetricValue]) -> str:
    """The table as JSON for the UI's "where does this number come from" view (values plus provenance)."""
    return json.dumps({k: v.to_dict() for k, v in table.items()}, ensure_ascii=False, sort_keys=True)

