"""Report number tooling: the model writes prose with placeholders, code writes every number.

    render(template, table)        {metric:<key>} / {metric:<key>:<period>} → formatted value (zh or en)
    verify_template(template, table)  the model's text may hold no figure outside a placeholder
    verify_numbers(text, table)    every number left in the final text must be a rendered table value

Keys are what the report builder chose for its table: usually the metric id, or `table_key(...)` for a
dimension row ("opex_actual@dept=sales") or a signal fact ("signal.overdue_receivable.A-0001.amount").
Allowed without a table entry: dates (2026-10-01, 2026-09, 2026-Q3, 2026年10月1日), years in a date
context ("in 2025", "FY2026", "September 2026"; a bare "2000 customers" is a figure),
ordinal list markers at the start of a line ("1." "2)"), quarter and half-year labels (Q3, H2), entity ids
such as A-0001 / P-02 / INV-00042, and [doc:…] citation markers. Everything else is a Problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

from corpai.metrics.model import NO_ACCESS, NO_ACCESS_ZH, MetricValue

PLACEHOLDER_RE = re.compile(r"\{metric:([A-Za-z0-9_.@=&\-]+)(?::(\d{4}-(?:\d{2}|Q[1-4])))?\}")
_ANY_PLACEHOLDER_RE = re.compile(r"\{metric:[^}]*\}")

SUPPRESSED_EN, SUPPRESSED_ZH = "[not shown: group too small]", "[小样本，不展示]"
NO_ACCESS_EN_TXT, NO_ACCESS_ZH_TXT = f"[{NO_ACCESS}]", f"[{NO_ACCESS_ZH}]"
UNDEFINED_EN, UNDEFINED_ZH = "[not defined for this period]", "[本期无数据]"


class RenderError(ValueError):
    """A placeholder names a key that is not in the table (the writer invented a metric)."""


@dataclass(frozen=True)
class Problem:
    kind: str      # unverified_number | unit_mismatch | unrendered_placeholder
    token: str
    start: int
    end: int
    message: str


def table_key(metric_id: str, dims: Mapping[str, str] | None = None) -> str:
    """Key for a dims row: 'opex_actual@dept=sales' (sorted, '&'-joined)."""
    if not dims:
        return metric_id
    return metric_id + "@" + "&".join(f"{k}={dims[k]}" for k in sorted(dims))


# ---- formatting ---------------------------------------------------------------------------------

def _q(v: Decimal, places: int) -> Decimal:
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def display_number(mv: MetricValue) -> Decimal | None:
    """The number as shown: EUR to whole euros, pct and days to one decimal, counts to integers."""
    if not mv.has_value:
        return None
    v = Decimal(mv.value)
    if mv.unit == "EUR":
        return _q(v, 0)
    if mv.unit in ("pct", "days"):
        return _q(v, 1)
    return _q(v, 0)


def _digits(v: Decimal) -> str:
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a == a.to_integral_value() and a.as_tuple().exponent >= 0:
        return f"{sign}{int(a):,}"
    places = max(0, -a.as_tuple().exponent)
    return f"{sign}{a:,.{places}f}"


def format_value(mv: MetricValue, lang: str = "en") -> str:
    zh = lang == "zh"
    if not mv.available:
        return NO_ACCESS_ZH_TXT if zh else NO_ACCESS_EN_TXT
    if mv.suppressed:
        return SUPPRESSED_ZH if zh else SUPPRESSED_EN
    d = display_number(mv)
    if d is None:
        return UNDEFINED_ZH if zh else UNDEFINED_EN
    s = _digits(d)
    if mv.unit == "EUR":
        return f"{s} 欧元" if zh else f"EUR {s}"
    if mv.unit == "pct":
        return f"{s}%"
    if mv.unit == "days":
        return f"{s} 天" if zh else f"{s} days"
    return s


def _lookup(table: Mapping[str, MetricValue], key: str, period: str | None) -> MetricValue:
    if period is not None:
        hit = table.get(f"{key}:{period}")
        if hit is not None:
            return hit
        hit = table.get(key)
        if hit is not None and hit.period == period:
            return hit
        raise RenderError(f"no table value for {key} in {period}")
    hit = table.get(key)
    if hit is None:
        raise RenderError(f"no table value for {key}")
    return hit


def render(template: str, table: Mapping[str, MetricValue], *, lang: str = "en") -> str:
    """Replace every {metric:<key>[:<period>]} with the formatted table value. Unknown key → RenderError.

    A value the reader may not see renders as "[no permission or not published]", a suppressed one as
    "[not shown: group too small]": explicit, never a zero."""
    def sub(m: re.Match) -> str:
        return format_value(_lookup(table, m.group(1), m.group(2)), lang)

    return PLACEHOLDER_RE.sub(sub, template)


# ---- verification -------------------------------------------------------------------------------
#
# Two checks, both run by the writer:
#   verify_template(template, table)  the model's own text, before rendering: it may contain no digit
#                                     outside a placeholder except dates, ids, list markers and the
#                                     constants of a metric's own name next to that metric. This is what
#                                     catches swapped, invented, scaled ("EUR 3M", "2000 万") and glued
#                                     ("EUR999999") figures: the model never writes a figure at all.
#                                     Direction words next to a variance ("23.5% below budget" for
#                                     +23.5 %) are checked against the value's sign.
#   verify_numbers(text, table)       the rendered text: every number left must be a displayed table
#                                     value of the right unit (defence in depth, and for text that did
#                                     not come from a template).

_MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
           r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_YEAR = r"(?:19|20)\d{2}"
_NOT_AMOUNT_AFTER = r"(?![.,]?\d)(?!\s*(?:%|％|欧元|万|亿|EUR|€|天|days?|[kKmM]\b|bn\b|mn\b|million|billion|thousand))"
_EXEMPT_PATTERNS = [
    re.compile(r"\[doc:[^\]]*\]"),                                   # citation markers
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                            # ISO dates
    re.compile(r"\b\d{4}-Q[1-4]\b"),                                 # 2026-Q3
    re.compile(r"\bQ[1-4]\s+\d{4}\b|\bH[12]\s+\d{4}\b"),             # Q3 2026, H2 2026
    re.compile(r"\b\d{4}-(?:0[1-9]|1[0-2])\b"),                      # 2026-09
    re.compile(r"\d{4}\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?)?"),  # 2026年10月1日
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日"),                      # 10月1日
    re.compile(r"(?<![\w-])(?:Q[1-4]|H[12])(?![\w-])"),              # Q3, H2
    re.compile(r"\b(?!EUR)[A-Z]{1,5}-\d{1,6}\b"),                    # entity ids: A-0001, P-02, INV-00042
    re.compile(r"\bL[1-4]\b"),                                       # data levels L1-L4
    re.compile(r"(?m)^[ \t]*(?:[#>]+[ \t]*)?\d{1,2}[.)](?=\s)"),     # ordinal list markers
    # Years only where the context says "a year": "in 2025", "since 2019", "FY2026", "September 2026",
    # "2025 and 2026" after such a word. A bare "2000 new customers" or "约 2000 万欧元" is a figure.
    re.compile(r"\b(?:in|since|until|till|by|from|during|before|after|of|for|year|fy|fiscal\s+year|end\s+of|"
               r"early|late|mid)\s+(?:the\s+(?:year\s+)?)?" + _YEAR + r"\b" + _NOT_AMOUNT_AFTER
               + r"(?:\s*(?:-|–|to|and|or)\s*" + _YEAR + r"\b" + _NOT_AMOUNT_AFTER + r")?", re.IGNORECASE),
    re.compile(r"\bFY\s?" + _YEAR + r"\b" + _NOT_AMOUNT_AFTER),
    re.compile(r"\b" + _MONTHS + r"\.?\s+" + _YEAR + r"\b" + _NOT_AMOUNT_AFTER, re.IGNORECASE),
]
# Any number, whatever is glued to it: "EUR999999", "3M", "12k", "15pct", "２０００" (after mapping).
_DIGITS_RE = re.compile(r"[-−+]?\d(?:[\d,.]*\d)?")
# The rendered-text scanner keeps its token boundaries for value matching ...
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.,])[-−+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![A-Za-z0-9_])"
                        r"|(?<![A-Za-z0-9_.])[-−+]?\d+(?:\.\d+)?(?![A-Za-z0-9_])")
# ... and treats a number glued to letters, or with a scale word, as never rendered by code.
_GLUED_BEFORE = re.compile(r"[A-Za-z€$£]$")
_GLUED_AFTER = re.compile(r"^[A-Za-z]")
_SCALE_AFTER = re.compile(r"^\s*(?:万|亿|千|[kKmM]\b|bn\b|mn\b|million|billion|thousand|tsd\b|mio\b|mrd\b)", re.I)
# Fullwidth digits would slip past the ASCII pattern; map them one-to-one so offsets stay valid.
_FULLWIDTH = str.maketrans({**{chr(0xFF10 + i): str(i) for i in range(10)}, "．": ".", "％": "%"})
_EUR_BEFORE = re.compile(r"(?:EUR|€)\s*$")
_EUR_AFTER = re.compile(r"^\s*(?:欧元|EUR|€)")
_PCT_AFTER = re.compile(r"^\s*(?:%|％|percent|pct\b|个百分点)")
_DAYS_AFTER = re.compile(r"^\s*(?:days?|天)")
# Clause ends: a full stop before a space (not a decimal point), ; ! ? and line ends. A colon is not one:
# "more than 60 days overdue: EUR 121,500" is one clause.
_SEGMENT_END = re.compile(r"\.(?=\s|$)|[;!?\n。；！？]")
_NEG_WORDS = re.compile(r"\b(?:below|under|lower|less|decreas\w*|down|fell|falls?|drop\w*|sav(?:ed|ing)|"
                        r"underspen\w*|short\s+of)\b|低于|减少|下降|节省|不足|少于|未达", re.I)
_POS_WORDS = re.compile(r"\b(?:above|over|higher|more|increas\w*|up|rose|rises?|grew|exceed\w*|overspen\w*|"
                        r"overrun\w*)\b|高于|超出|超过|超支|增加|上升|多于", re.I)


def _kind(text: str, start: int, end: int) -> str:
    before, after = text[max(0, start - 6):start], text[end:end + 6]
    if _EUR_BEFORE.search(before) or _EUR_AFTER.match(after):
        return "EUR"
    if _PCT_AFTER.match(after):
        return "pct"
    if _DAYS_AFTER.match(after):
        return "days"
    return "num"


def _parse(token: str) -> Decimal:
    return abs(Decimal(token.replace(",", "").replace("−", "-")))


def allowed_values(table: Mapping[str, MetricValue]) -> set[tuple[str, Decimal]]:
    """(unit, displayed number) for every value in the table, as render() would print it."""
    out: set[tuple[str, Decimal]] = set()
    for mv in table.values():
        d = display_number(mv)
        if d is not None:
            out.add((mv.unit if mv.unit != "count" else "num", abs(d)))
    return out


def _key_patterns(table: Mapping[str, MetricValue]) -> list[re.Pattern[str]]:
    """Table and signal keys named in prose ("ar_overdue_60d") are identifiers, not figures."""
    return [re.compile(r"(?<![\w.])" + re.escape(k) + r"(?![\w])") for k in sorted(table, key=len, reverse=True)]


def _mask(text: str, patterns: list[re.Pattern[str]]) -> str:
    masked = list(text)
    for pat in patterns:
        for m in pat.finditer("".join(masked)):
            for i in range(m.start(), m.end()):
                masked[i] = " "
    return "".join(masked)


def _name_constants(table: Mapping[str, MetricValue]) -> dict[Decimal, list[tuple[str, MetricValue]]]:
    """Numbers inside a metric's own name ("more than 60 days"), with the unit they carry there."""
    out: dict[Decimal, list[tuple[str, MetricValue]]] = {}
    for mv in table.values():
        for nm in {mv.name_en, mv.name_zh} - {None, ""}:
            for m in _NUMBER_RE.finditer(nm):
                try:
                    out.setdefault(_parse(m.group(0)), []).append((_kind(nm, m.start(), m.end()), mv))
                except ArithmeticError:
                    continue
    return out


def _segment(text: str, start: int, end: int) -> tuple[int, int]:
    """The clause around [start, end): up to the nearest sentence or line boundary on each side."""
    lo = max((m.end() for m in _SEGMENT_END.finditer(text, 0, start)), default=0)
    m = _SEGMENT_END.search(text, end)
    return lo, (m.start() if m else len(text))


def _near_own_metric(text: str, start: int, end: int, kind: str, value: Decimal,
                     consts: Mapping[Decimal, list[tuple[str, MetricValue]]], anchors: Mapping[int, list[str]]) -> bool:
    """A name constant is allowed only in the same clause as its own metric (placeholder key or rendered
    value), and only with the unit it has in the name (or none): "more than 60 days overdue: EUR 121,500"
    passes, "opex rose 60%" does not."""
    lo, hi = _segment(text, start, end)
    for name_kind, mv in consts.get(value, []):
        if kind not in ("num", name_kind):
            continue
        for key_pos, tokens in anchors.items():
            if lo <= key_pos < hi and any(t in tokens for t in (mv.metric_id, id(mv))):
                return True
    return False


def _anchor_index(text: str, table: Mapping[str, MetricValue], *, rendered: bool) -> dict[int, list]:
    """Positions where a metric is cited: placeholders (template) or rendered values (final text)."""
    out: dict[int, list] = {}
    if not rendered:
        for m in PLACEHOLDER_RE.finditer(text):
            mv = table.get(m.group(1)) or table.get(f"{m.group(1)}:{m.group(2)}")
            if mv is not None:
                out.setdefault(m.start(), []).extend([mv.metric_id, id(mv)])
        return out
    for mv in table.values():
        for lang in ("en", "zh"):
            shown = format_value(mv, lang)
            for m in re.finditer(re.escape(shown), text):
                out.setdefault(m.start(), []).extend([mv.metric_id, id(mv)])
    return out


def _is_variance(key: str, mv: MetricValue) -> bool:
    return mv.unit == "pct" and ("variance" in mv.metric_id or key.endswith((".over_pct", ".overrun_pct"))
                                 or "vs budget" in (mv.name_en or "").lower())


def _direction_problems(template: str, table: Mapping[str, MetricValue]) -> list[Problem]:
    """A signed variance cited with the opposite direction word in its clause ("23.5% below budget" for
    +23.5 %). The value's sign comes from code; the model only chose the word."""
    out: list[Problem] = []
    for m in PLACEHOLDER_RE.finditer(template):
        key = m.group(1)
        mv = table.get(key) or table.get(f"{key}:{m.group(2)}")
        if mv is None or not _is_variance(key, mv):
            continue
        d = display_number(mv)
        if d is None or d == 0:
            continue
        lo, hi = _segment(template, m.start(), m.end())
        clause = PLACEHOLDER_RE.sub(" ", template[lo:m.start()] + " " + template[m.end():hi])
        neg, pos = bool(_NEG_WORDS.search(clause)), bool(_POS_WORDS.search(clause))
        if (d > 0 and neg and not pos) or (d < 0 and pos and not neg):
            out.append(Problem("direction_mismatch", m.group(0), m.start(), m.end(),
                               f"the text says {'below' if d > 0 else 'above'} but the value is "
                               f"{'above' if d > 0 else 'below'} zero"))
    return out


def verify_template(template: str, table: Mapping[str, MetricValue]) -> list[Problem]:
    """The model's text before rendering: no figure may be written outside a placeholder.

    Allowed digits: dates and years in a date context, entity ids, data levels, list markers, citation
    markers, and the constants of a metric's own name in the same clause as that metric. Anything else
    (an invented, swapped, scaled or glued number) is a Problem. Variance direction words must agree
    with the value's sign."""
    text = template.translate(_FULLWIDTH)
    # Table keys and signal keys may be named in prose ("ar_overdue_60d"); they are identifiers, not figures.
    scan = _mask(text, [_ANY_PLACEHOLDER_RE, *_key_patterns(table), *_EXEMPT_PATTERNS])
    consts = _name_constants(table)
    anchors = _anchor_index(text, table, rendered=False)
    problems: list[Problem] = []
    for m in _DIGITS_RE.finditer(scan):
        if _name_constant_ok(text, m, consts, anchors):
            continue
        problems.append(Problem("number_outside_placeholder", m.group(0), m.start(), m.end(),
                                "a figure must be a {metric:...} placeholder, never written as digits"))
    problems += _direction_problems(text, table)
    return sorted(problems, key=lambda p: p.start)


def _name_constant_ok(text: str, m: re.Match[str], consts: Mapping[Decimal, list[tuple[str, MetricValue]]],
                      anchors: Mapping[int, list]) -> bool:
    try:
        value = _parse(m.group(0))
    except ArithmeticError:
        return False
    return value in consts and _near_own_metric(text, m.start(), m.end(), _kind(text, m.start(), m.end()),
                                                value, consts, anchors)


def verify_numbers(text: str, table: Mapping[str, MetricValue]) -> list[Problem]:
    """Every number in the final text must be the displayed form of a table value (sign is free: prose may
    say "down 3.2%"; verify_template checks direction words). A number with a unit must match a value of
    that unit; a bare number may match any. A number glued to letters ("EUR999999", "12k") or followed by
    a scale word ("3 M", "2000 万") is never a rendered value. Constants from a metric's name count only
    in the same clause as that metric's value. Leftover placeholders are problems too. Numbers spelled out
    in words are not detected."""
    text = text.translate(_FULLWIDTH)
    problems: list[Problem] = []
    for m in _ANY_PLACEHOLDER_RE.finditer(text):
        problems.append(Problem("unrendered_placeholder", m.group(0), m.start(), m.end(),
                                "placeholder left in the final text"))
    scan = _mask(text, [_ANY_PLACEHOLDER_RE, *_key_patterns(table), *_EXEMPT_PATTERNS])
    allowed = allowed_values(table)
    by_value: dict[Decimal, set[str]] = {}
    for unit, v in allowed:
        by_value.setdefault(v, set()).add(unit)
    consts = _name_constants(table)
    anchors = _anchor_index(text, table, rendered=True)
    seen: set[tuple[int, int]] = set()
    for m in _DIGITS_RE.finditer(scan):
        token = m.group(0).lstrip("+-−")
        start = m.end() - len(token)
        before, after = scan[max(0, start - 1):start], scan[m.end():m.end() + 12]
        if _GLUED_BEFORE.search(before) or _GLUED_AFTER.match(after) or _SCALE_AFTER.match(after):
            problems.append(Problem("unverified_number", token, start, m.end(),
                                    "number glued to letters or scaled: never a value rendered by code"))
            seen.add((start, m.end()))
    for m in _NUMBER_RE.finditer(scan):
        token = m.group(0)
        if (m.end() - len(token.lstrip("+-−")), m.end()) in seen:
            continue
        try:
            value = _parse(token)
        except ArithmeticError:
            problems.append(Problem("unverified_number", token, m.start(), m.end(), "unparseable number"))
            continue
        kind = _kind(text, m.start(), m.end())
        units = by_value.get(value, set())
        if units and (kind == "num" or kind in units):
            continue
        if value in consts and _near_own_metric(text, m.start(), m.end(), kind, value, consts, anchors):
            continue
        if not units:
            problems.append(Problem("unverified_number", token, m.start(), m.end(),
                                    "number is not a value from the metric table"))
        else:
            problems.append(Problem("unit_mismatch", token, m.start(), m.end(),
                                    f"number is in the table but not as {kind}"))
    return sorted(problems, key=lambda p: p.start)
