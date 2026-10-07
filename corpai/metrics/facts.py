"""Ids and dates in the report writer's text: only those the model was shown (design-review 二.8).

numbers.verify_template lets dates, periods and entity ids through because they are not figures. They
are still facts a reader acts on: a contract id, a notice deadline, an overdue customer. A model that
names the wrong contract or invents a deadline would pass the number checks, so this module checks the
other half:

    verify_facts(template, table, shown)   every id or date in the model's text is one it was given
                                           (the prompt: report title, placeholder keys, issue list)
    missing_issues(text, signals)          every issue found by code is named in the text by its ref

Dates are compared in normalised form, so "2026-10-31", "2026年10月31日" and "10月31日" are the same
fact; a month or quarter is allowed when it is shown or contains a shown date, a year when any shown
date or period lies in it.
"""

from __future__ import annotations

import re
from typing import Iterator, Mapping

from corpai.metrics.model import MetricValue
from corpai.metrics.numbers import _ANY_PLACEHOLDER_RE, Problem, _key_patterns, _mask
from corpai.metrics.signals import SignalResult

_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_WORD = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
               r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_YEAR = r"((?:19|20)\d{2})"
ID_RE = re.compile(r"\b(?!EUR-)[A-Z]{1,5}-\d{1,6}\b")

# (kind, pattern) in the order they are taken; each match is blanked before the next pattern runs, so
# "2026-10-31" is one day, not also the month 2026-10.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("day", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
    ("day", re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")),
    ("month", re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")),
    ("year", re.compile(r"(\d{4})\s*年")),
    ("monthday", re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")),
    ("quarter", re.compile(r"\b(\d{4})-Q([1-4])\b")),
    ("quarter_rev", re.compile(r"\bQ([1-4])\s+(\d{4})\b")),
    ("half", re.compile(r"\bH[12]\s+(\d{4})\b")),
    ("month", re.compile(r"\b(\d{4})-(0[1-9]|1[0-2])\b")),
    ("month_word", re.compile(r"\b" + _MONTH_WORD + r"\.?\s+" + _YEAR + r"\b", re.I)),
    ("year", re.compile(r"\bFY\s?" + _YEAR + r"\b", re.I)),
    # any other year the number checks let through (they allow years only in a date context)
    ("year", re.compile(r"(?<![\d.,-])" + _YEAR + r"(?!\d|[.,]\d|-\d)")),
    ("id", ID_RE),
]


def _norm(kind: str, m: re.Match[str]) -> tuple[str, str]:
    g = m.groups()
    if kind == "day":
        return "day", f"{int(g[0]):04d}-{int(g[1]):02d}-{int(g[2]):02d}"
    if kind == "month":
        return "month", f"{int(g[0]):04d}-{int(g[1]):02d}"
    if kind == "month_word":
        return "month", f"{int(g[1]):04d}-{_MONTH_NAMES.index(g[0][:3].lower()) + 1:02d}"
    if kind == "monthday":
        return "monthday", f"{int(g[0]):02d}-{int(g[1]):02d}"
    if kind == "quarter":
        return "quarter", f"{int(g[0]):04d}-Q{g[1]}"
    if kind == "quarter_rev":
        return "quarter", f"{int(g[1]):04d}-Q{g[0]}"
    if kind in ("year", "half"):
        return "year", f"{int(g[0]):04d}"
    return "id", m.group(0)


def _facts(text: str) -> Iterator[tuple[str, str, re.Match[str]]]:
    """(kind, normalised value, match) for every id and date in `text`."""
    scan = text
    for kind, pat in _PATTERNS:
        for m in pat.finditer(scan):
            yield (*_norm(kind, m), m)
        scan = _mask(scan, [pat])


def allowed_facts(shown: str) -> dict[str, set[str]]:
    """What the model may write: the ids and dates of the prompt, with the months, quarters and years
    that contain a shown date (a deadline on 2026-10-31 lets the text say "October 2026" or "2026-Q4")."""
    out: dict[str, set[str]] = {k: set() for k in ("day", "month", "monthday", "quarter", "year", "id")}
    for kind, value, _ in _facts(shown):
        out[kind].add(value)
    for day in list(out["day"]):
        out["month"].add(day[:7])
        out["monthday"].add(day[5:])
    for q in list(out["quarter"]):
        first = 3 * (int(q[-1]) - 1) + 1
        out["month"] |= {f"{q[:4]}-{m:02d}" for m in range(first, first + 3)}
    for month in list(out["month"]):
        out["quarter"].add(f"{month[:4]}-Q{(int(month[5:]) - 1) // 3 + 1}")
    out["year"] |= {v[:4] for k in ("day", "month", "quarter") for v in out[k]}
    return out


def verify_facts(template: str, table: Mapping[str, MetricValue], shown: str) -> list[Problem]:
    """Ids and dates in the model's text that were not in what it was shown (`shown`: the prompt).
    Placeholders and table keys named in prose are identifiers of the table, not facts, and are skipped."""
    allowed = allowed_facts(shown)
    scan = _mask(template, [_ANY_PLACEHOLDER_RE, *_key_patterns(table)])
    problems = [Problem("unverified_fact", m.group(0), m.start(), m.end(),
                        f"this {kind} is not among the facts given; write only ids and dates listed under ISSUES")
                for kind, value, m in _facts(scan) if value not in allowed[kind]]
    return sorted(problems, key=lambda p: p.start)


def missing_issues(text: str, signals: Mapping[str, SignalResult]) -> list[Problem]:
    """An issue code found and the reader may see, but the text never names it by its ref."""
    out: list[Problem] = []
    for kind, s in signals.items():
        if s.status != "ok":
            continue
        for f in s.findings:
            ref = str(f.get("ref") or "")
            if ref and not re.search(r"(?<![\w-])" + re.escape(ref) + r"(?![\w-])", text):
                out.append(Problem("missing_issue", ref, 0, 0, f"the issue {kind} {ref} is not mentioned"))
    return out
