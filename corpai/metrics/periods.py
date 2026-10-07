"""Reporting periods: "YYYY-MM" (month) or "YYYY-Qn" (quarter), and the SQL parameters they imply."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


@dataclass(frozen=True)
class Period:
    text: str
    grain: str  # "month" | "quarter"
    start: date
    end: date  # exclusive

    @property
    def months(self) -> list[str]:
        out, d = [], self.start
        while d < self.end:
            out.append(f"{d.year:04d}-{d.month:02d}")
            d = _add_months(d, 1)
        return out

    @property
    def as_of(self) -> date:
        """The reporting date: balances are taken at the start of the first day after the period."""
        return self.end

    def params(self) -> dict:
        next_start = _add_months(date(self.as_of.year, self.as_of.month, 1), 1)
        return {
            "start": self.start, "end": self.end, "as_of": self.as_of, "periods": self.months,
            "next_start": next_start, "next_end": _add_months(next_start, 1),
            "ttm_start": _add_months(self.end, -12), "days": (self.end - self.start).days,
        }


def parse_period(text: str) -> Period:
    """'2026-09' or '2026-Q3'; anything else raises ValueError (periods end up in SQL parameters and ids)."""
    if not isinstance(text, str):
        raise ValueError("period must be a string like 2026-09 or 2026-Q3")
    m = _MONTH_RE.match(text)
    if m:
        start = date(int(m.group(1)), int(m.group(2)), 1)
        return Period(text, "month", start, _add_months(start, 1))
    q = _QUARTER_RE.match(text)
    if q:
        start = date(int(q.group(1)), 3 * (int(q.group(2)) - 1) + 1, 1)
        return Period(text, "quarter", start, _add_months(start, 3))
    raise ValueError(f"bad period {text!r}: use YYYY-MM or YYYY-Qn")


def quarter_before(d: date) -> Period:
    """The last complete quarter that ends on or before day d (2026-10-01 → 2026-Q3)."""
    last = d - timedelta(days=1)
    q = (last.month - 1) // 3 + 1
    p = parse_period(f"{last.year}-Q{q}")
    if p.end > d:  # d lies inside that quarter: step back one more
        prev = _add_months(p.start, -3)
        p = parse_period(f"{prev.year}-Q{(prev.month - 1) // 3 + 1}")
    return p


def month_before(d: date) -> Period:
    """The last complete month before day d (2026-10-01 → 2026-09)."""
    last = date(d.year, d.month, 1) - timedelta(days=1)
    p = parse_period(f"{last.year:04d}-{last.month:02d}")
    return p
