"""What the user asked for, read by code: the reporting period and the language.

The input box is free text ("2026-Q3 管理层报告", "report for September 2026"). Only a period and a
language are taken from it; the text itself never reaches a model or a query. Nothing recognisable
means the last complete quarter before the simulation date (2026-10-01 → 2026-Q3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from corpai.metrics import parse_period, quarter_before

_CJK = re.compile(r"[㐀-鿿]")
_LATIN = re.compile(r"[A-Za-z]{2,}")
_QUARTER = re.compile(r"\b(\d{4})\s*-?\s*Q([1-4])\b|\bQ([1-4])\s*(?:/|-)?\s*(\d{4})\b", re.I)
_MONTH = re.compile(r"\b(\d{4})-(0[1-9]|1[0-2])\b")
_ZH_QUARTER = re.compile(r"(\d{4})\s*年\s*(?:第\s*)?([1-4一二三四])\s*季度")
_ZH_MONTH = re.compile(r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月")
_MONTHS_EN = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
              "october", "november", "december")
_EN_MONTH = re.compile(r"\b(" + "|".join(m[:3] + r"(?:" + m[3:] + r")?" for m in _MONTHS_EN) + r")\.?\s+(\d{4})\b",
                       re.I)
_ZH_DIGITS = {"一": "1", "二": "2", "三": "3", "四": "4"}


@dataclass(frozen=True)
class ReportRequest:
    period: str      # YYYY-MM or YYYY-Qn
    lang: str        # zh | en
    explicit: bool   # the period came from the input, not the default


def _period_in(text: str) -> str | None:
    m = _QUARTER.search(text)
    if m:
        year, q = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        return f"{year}-Q{q}"
    m = _ZH_QUARTER.search(text)
    if m:
        return f"{m.group(1)}-Q{_ZH_DIGITS.get(m.group(2), m.group(2))}"
    m = _MONTH.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _ZH_MONTH.search(text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = _EN_MONTH.search(text)
    if m:
        month = next(i for i, name in enumerate(_MONTHS_EN, 1) if name.startswith(m.group(1).lower()[:3]))
        return f"{m.group(2)}-{month:02d}"
    return None


def parse_request(text: str, today: date) -> ReportRequest:
    """Period and language from the input. A period that has not ended yet (or is not a real period)
    is refused with ValueError: its figures would be partial, and a report must not look final."""
    text = text or ""
    lang = "zh" if _CJK.search(text) or not _LATIN.search(text) else "en"
    found = _period_in(text)
    if found is None:
        return ReportRequest(period=quarter_before(today).text, lang=lang, explicit=False)
    p = parse_period(found)  # ValueError for 2026-13 and the like
    if p.end > today:
        raise ValueError(f"the period {p.text} has not ended yet; pick a period that ended by {today.isoformat()}")
    return ReportRequest(period=p.text, lang=lang, explicit=True)
