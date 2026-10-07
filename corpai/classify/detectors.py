"""Content detectors behind classify_text (architecture §7, design-review §4 rules 1 and 4).

Every pattern is bounded, and window checks ("money near a commercial word") use sorted
positions and bisect, so classification stays linear: about 3 ms for a 4 kB message.

Numeric detectors (IBAN, ids, cards, phones, money) claim their spans in a fixed order so that one
digit run is reported once, and so that an invalid IBAN or an e-mail address is not re-read as a
phone number.
"""

from __future__ import annotations

import bisect
import re
from typing import Callable

from corpai.types import Level

from . import names, protected
from .model import Finding
from .normalise import map_span, normalised
from .secrets import find_secrets

L1, L2, L3, L4 = Level.L1, Level.L2, Level.L3, Level.L4

# ---- patterns -------------------------------------------------------------------------------

CANARY = re.compile(r"KB-CANARY-[0-9A-F]{8}")
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
                   r"\.[A-Za-z]{2,24}(?![A-Za-z0-9-])")

# IBAN candidates: country + check digits + 4-char groups (spaced or compact). Validated by mod-97.
IBAN_CAND = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?(?![A-Za-z0-9])")
IBAN_LEN = {"AT": 20, "BE": 16, "CH": 21, "CZ": 24, "DE": 22, "DK": 18, "ES": 24, "FI": 18, "FR": 27,
            "GB": 22, "IE": 22, "IT": 27, "LU": 20, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "SE": 24}

CN_ID = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
TAX_NUM = re.compile(r"(?<![\d\w-])(?:\d{11}|\d{2} \d{3} \d{3} \d{3})(?![\d\w-])")
TAX_CONTEXT = re.compile(
    r"(?i)steuer[- ]?id(?:entifikationsnummer)?|steuerliche identifikationsnummer|identifikationsnummer|"
    r"\bidnr\b|\btax[- ]?id\b|tax identification(?: number)?|\btax number\b|steuernummer|"
    r"national id|personalausweis\w*|ausweisnummer|身份证\w*|税号|纳税人识别号|\bTIN\b")

CARD = re.compile(r"(?<![\d+-])(?:[2-6]\d{12,18}|[2-6]\d{3}(?:[ -]\d{4}){2,3}(?:[ -]\d{1,3})?|"
                  r"3[47]\d{2}[ -]\d{6}[ -]\d{5})(?![\d-])")

# International: +<cc> (any country) or 00<cc> for our home country (46) and the countries we trade
# with, then 6-12 digits.
PHONE_INTL = re.compile(r"(?<![\w+])(?:\+[1-9]\d{0,2}|00(?:46|49|31|33|48|43|39|34|41|32|44|86))"
                        r"(?:[ \-./]?\(0\))?(?:[ \-./]?\d){6,12}(?!\d)")
# National: leading 0 + area code, at least one separator-free run; digit count checked in code.
PHONE_NAT = re.compile(r"(?<![\w+\-/.,])(?<!\d )0[1-9]\d{0,4}(?:[ /-]|\s?/\s?)\d{2,8}(?:[ -]\d{2,5}){0,3}(?![\w-]|\.\d)"
                       r"|(?<![\w+\-/.,])0[1-9](?:[ .]\d{2}){4}(?![\w.-])"  # French 01 23 45 67 89
                       r"|(?<![\w+\-/.,])01[5-7]\d{8,9}(?!\d)")  # compact German mobile
PHONE_CN = re.compile(r"(?<![\d+])(?:(?:\+|00)86[ -]?)?1[3-9]\d(?:[ -]?\d{4}){2}(?!\d)")

_CUR_PRE = r"(?:€|\$|£|¥|￥|US\$|EUR|USD|GBP|CHF|PLN|CNY|RMB)"
_CUR_POST = r"(?:€|EUR|[Ee]uros?|USD|\$|[Dd]ollars?|GBP|£|CHF|PLN|zł|CNY|RMB|万元|亿元|元|人民币|块钱?)"
_AMOUNT = r"(?:\d{1,3}(?:[.,'\u00a0\u202f ]\d{3}){1,6}(?:[.,]\d{1,2})?|\d{1,12}(?:[.,]\d{1,2})?)"
_MULT = r"(?:\s?(?:k|K|m|M|bn|Mio\.?|Mrd\.?|million|millions|billion|Tsd\.?|thousand|Millionen|Milliarden)(?![A-Za-z]))?"
MONEY = re.compile(rf"(?<![A-Za-z0-9.,]){_CUR_PRE}\s?{_AMOUNT}{_MULT}(?!\d)"
                   rf"|(?<![A-Za-z0-9.,\-]){_AMOUNT}{_MULT}\s?{_CUR_POST}(?![A-Za-z])")

COMMERCIAL = re.compile(
    r"(?i)\b(?:discount\w*|quot(?:e|es|ed|ation|ations)|pric(?:e|es|ed|ing)|invoic\w*|margins?|revenues?|"
    r"budget\w*|deals?|rabatt\w*|angebot\w*|preis\w*|rechnung\w*|umsatz\w*|"
    # contract money: a liability cap or an indemnity amount is a negotiated term (design-review 四.3
    # "pasted customer contract clauses: L3"); contracts and playbooks are L3 by source anyway
    r"liabilit(?:y|ies)|indemnit\w*|haftung\w*)\b|\bARR\b|折扣|报价|预算|收入|价格|发票|责任上限|赔偿")

SALARY_TERM = re.compile(
    r"(?i)\b(?:salar(?:y|ies)|\w{0,12}geh(?:a|ä)lt(?:er|s)?|compensation|bonus(?:es)?|wages?|remuneration|"
    r"vergütung\w*|(?:base|annual|monthly|gross|net|take-home)\s+pay|pay\s+(?:rise|raise|increase|band|grade|cut|"
    r"scale|is|was|of))\b|薪资|工资|年薪|月薪|薪酬|薪水|奖金|底薪")
# Numbers that can be a salary: grouped thousands, 3+ digits, or 30k / 30万. Not "FP-1234", not years.
SALARY_NUM = re.compile(r"(?<![A-Za-z\d.,-])(?:\d{1,3}(?:[.,' ]\d{3}){1,4}(?:[.,]\d{1,2})?|"
                        r"\d+(?:[.,]\d+)?\s?(?:k|K|Tsd\.?|万|千)|\d{3,9}(?:[.,]\d{1,2})?)(?!\d)")
_YEAR = re.compile(r"(?:19|20)\d{2}")

PERFORMANCE = re.compile(r"(?i)\bperformance\s+(?:ratings?|reviews?|scores?|evaluations?|appraisals?|"
                         r"improvement plans?)\b|\bleistungsbeurteilung\w*|\bleistungsbewertung\w*|"
                         r"\bmitarbeiterbeurteilung\w*|绩效")
VULN = re.compile(r"(?i)\b(?:vulnerabilit(?:y|ies)|exploit\w*|RCE|remote code execution|SQL[- ]?injection|"
                  r"XSS|privilege escalation|sicherheitslücke\w*|schwachstelle\w*)\b|\bCVE-\d{4}-\d{3,7}\b|漏洞")
LITIGATION = re.compile(r"(?i)\b(?:lawsuits?|litigation|litigat\w+|klage(?:n|schrift)?|verklag\w*|"
                        r"rechtsstreit\w*|gerichtsverfahren)\b|诉讼|起诉")
MATTER_ID = re.compile(r"\bM-\d{1,6}\b")
# Our own names: the company ABC Tech (also "abctech" in hosts and code), the product, and the kb_
# prefix of internal packages (kept from the former company name, Kestrelbyte). The two words of
# "ABC Tech" may be split by a line wrap or extra spaces, or joined in CamelCase code (AbcTechClient,
# new ABCTechApi(), getAbcTech()). A lowercase continuation is another name ("ABC Technoquill",
# "abctechnology"), and so is an all-caps one ("ABC TECHNOQUILL").
_ABC_TECH = (r"(?:(?<![A-Za-z0-9])(?i:abc)|(?<=[a-z0-9])A(?i:bc))[\s_-]{0,3}"
             r"(?:TECH(?![A-Za-z0-9])|(?!TECH)(?i:tech)(?![a-z0-9]))")
COMPANY = re.compile(_ABC_TECH + r"|(?i:fleetpilot)|(?<![A-Za-z0-9])(?i:kb_[a-z0-9_]{1,64})")

_FENCE = re.compile(r"^[ \t]{0,3}(```|~~~)")
_INDENTED = re.compile(r"^(?: {4}|\t)[ \t]*\S")
_CODE_LINE = re.compile(
    r"^\s*(?:def\s+\w+\s*\(|class\s+\w+\s*[:(]|import\s+[\w.]+|from\s+[\w.]+\s+import\s|return\b|"
    r"function\s+\w+\s*\(|(?:const|let|var)\s+\w+\s*=|(?:public|private|protected)\s+\w|#include\s*[<\"]|"
    r"package\s+[\w.]+;?$|func\s+\w+\s*\(|SELECT\s.{1,200}\sFROM\s|INSERT\s+INTO\s|CREATE\s+TABLE\s|}\s*$)"
    r"|[;{]\s*$")


# ---- validators -----------------------------------------------------------------------------

def iban_valid(compact: str) -> bool:
    """ISO 13616 mod-97 check. Only valid IBANs count: random letter-digit codes are not bank data."""
    if not (15 <= len(compact) <= 34) or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    want = IBAN_LEN.get(compact[:2])
    if want is not None and len(compact) != want:
        return False
    digits = "".join(str(int(ch, 36)) for ch in compact[4:] + compact[:4])
    return int(digits) % 97 == 1


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_CN_W = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)


def cn_id_valid(s: str) -> bool:
    """GB 11643 checksum of an 18-character resident identity number."""
    s = s.upper()
    total = sum(int(c) * w for c, w in zip(s[:17], _CN_W))
    return "10X98765432"[total % 11] == s[17]


# ---- helpers --------------------------------------------------------------------------------

class _Claims:
    """Non-overlapping claimed spans, so one digit run is reported by one detector."""

    def __init__(self) -> None:
        self._starts: list[int] = []
        self._ends: list[int] = []

    def overlaps(self, s: int, e: int) -> bool:
        i = bisect.bisect_right(self._starts, s)
        if i > 0 and self._ends[i - 1] > s:
            return True
        return i < len(self._starts) and self._starts[i] < e

    def claim(self, s: int, e: int) -> bool:
        if self.overlaps(s, e):
            return False
        i = bisect.bisect_right(self._starts, s)
        self._starts.insert(i, s)
        self._ends.insert(i, e)
        return True


def _near(positions: "_Positions", s: int, e: int, window: int) -> tuple[int, int] | None:
    """The span in `positions` closest to [s, e) within `window` chars, if any."""
    if not positions:
        return None
    # The spans are short tokens (< 200 chars), so starting the scan 200 chars early is enough.
    lo = bisect.bisect_left(positions.starts, s - window - 200)
    best: tuple[int, int] | None = None
    best_gap = window + 1
    for i in range(lo, len(positions)):  # indexing, not slicing: a slice copies the tail (quadratic)
        ps, pe = positions[i]
        if ps > e + window:
            break
        gap = 0 if (ps < e and pe > s) else (ps - e if ps >= e else s - pe)
        if gap <= window and gap < best_gap:
            best, best_gap = (ps, pe), gap
    return best


class _Positions(list):
    """A list of (start, end) spans sorted by start, with the starts cached for bisect."""

    def __init__(self, spans: list[tuple[int, int]]) -> None:
        super().__init__(sorted(spans))
        self.starts = [s for s, _ in self]


def _code_blocks(text: str) -> list[tuple[int, int]]:
    """Fenced blocks, runs of 3+ indented lines, and runs of 2+ lines that look like code."""
    blocks: list[tuple[int, int]] = []
    pos = 0
    fence_start: int | None = None
    fence_tok = ""
    ind_start = ind_end = -1
    ind_count = 0
    code_start = code_end = -1
    code_count = 0

    def flush_indent() -> None:
        nonlocal ind_count
        if ind_count >= 3:
            blocks.append((ind_start, ind_end))
        ind_count = 0

    def flush_code() -> None:
        nonlocal code_count
        if code_count >= 2:
            blocks.append((code_start, code_end))
        code_count = 0

    for line in text.splitlines(keepends=True):
        start, end = pos, pos + len(line)
        pos = end
        body = line.rstrip("\r\n")
        fm = _FENCE.match(body)
        if fence_start is not None:
            if fm and fm.group(1) == fence_tok:
                blocks.append((fence_start, end))
                fence_start = None
            continue
        if fm:
            flush_indent()
            flush_code()
            fence_start, fence_tok = start, fm.group(1)
            continue
        if not body.strip():
            continue  # blank lines do not break an indented or code run
        if _INDENTED.match(body):
            if ind_count == 0:
                ind_start = start
            ind_count += 1
            ind_end = end
        else:
            flush_indent()
        if len(body) <= 400 and _CODE_LINE.search(body):
            if code_count == 0:
                code_start = start
            code_count += 1
            code_end = end
        else:
            flush_code()
    if fence_start is not None:  # unterminated fence: the rest is code
        blocks.append((fence_start, len(text)))
    flush_indent()
    flush_code()
    return blocks


# ---- the detector pipeline ------------------------------------------------------------------

_DIGIT = re.compile(r"\d")


def _numeric(text: str, claims: _Claims, add: Callable[..., None]) -> None:
    """IBAN, ids, cards, phones, money and salary, in claim order. Context words are only
    searched when a candidate number exists, which keeps ordinary prose cheap."""
    for m in IBAN_CAND.finditer(text):
        if claims.overlaps(*m.span()):
            continue
        compact = m.group().replace(" ", "")
        want = IBAN_LEN.get(compact[:2])
        if want is not None and len(compact) > want:  # greedy match ran into the next token
            compact = compact[:want]
        if iban_valid(compact):
            add("iban", m.start(), m.end(), L3)
        else:
            claims.claim(*m.span())  # invalid candidates are claimed too: their digits are not phones

    for m in CN_ID.finditer(text):
        if not claims.overlaps(*m.span()) and cn_id_valid(m.group()):
            add("tax_id", m.start(), m.end(), L3)
    tax_cands = [m for m in TAX_NUM.finditer(text) if m.group()[0] != "0" and not claims.overlaps(*m.span())]
    if tax_cands:
        ctx = _Positions([mm.span() for mm in TAX_CONTEXT.finditer(text)])
        for m in tax_cands:
            if _near(ctx, *m.span(), 40) and not claims.overlaps(*m.span()):
                add("tax_id", m.start(), m.end(), L3)
    for m in CARD.finditer(text):
        s, e = m.span()
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and not claims.overlaps(s, e) and luhn_valid(digits):
            add("card", s, e, L3)

    for pat in (PHONE_INTL, PHONE_CN, PHONE_NAT):
        for m in pat.finditer(text):
            s, e = m.span()
            if claims.overlaps(s, e):
                continue
            if pat is PHONE_NAT and not (9 <= sum(ch.isdigit() for ch in m.group()) <= 13):
                continue  # dates, short codes
            add("phone", s, e, L3)

    money = [m.span() for m in MONEY.finditer(text) if not claims.overlaps(*m.span())]
    if money:
        commercial = _Positions([mm.span() for mm in COMMERCIAL.finditer(text)])
        for s, e in money:
            add("money", s, e, L3 if _near(commercial, s, e, 60) else L2)

    nums = list(money)
    for m in SALARY_NUM.finditer(text):
        if not _YEAR.fullmatch(m.group()) and not claims.overlaps(*m.span()):
            nums.append(m.span())
    if nums:
        numpos = _Positions(nums)
        for mm in SALARY_TERM.finditer(text):
            ts, te = mm.span()
            hit = _near(numpos, ts, te, 40)
            if hit:
                add("salary", min(ts, hit[0]), max(te, hit[1]), L4, False)


# Hidden characters (zero-width, bidi, fullwidth): see normalise.py. Text containing any of them is
# scanned a second time in normalised form, with offsets mapped back to the original.
_normalised = normalised


def detect(text: str) -> list[Finding]:
    """All findings, including those only visible after removing hiding characters."""
    if not isinstance(text, str):
        raise TypeError("classify_text expects str")
    found = _detect(text)
    norm = _normalised(text)
    if norm is not None:
        ntext, index = norm
        # Keyed by level too: the normalised pass can see the same span at a higher level (company
        # code once "fleet<ZWSP>pilot" reads "fleetpilot", money once "dis<SHY>count" reads
        # "discount"); dropping it as a duplicate would keep the lower level (W-M1 robustness eval).
        seen = {(f.kind, f.start, f.end, f.level) for f in found}
        for f in _detect(ntext):
            s, e = map_span(index, f.start, f.end)
            if (f.kind, s, e, f.level) not in seen:
                seen.add((f.kind, s, e, f.level))
                found.append(Finding(f.kind, s, e, f.level))
    return found


def _detect(text: str) -> list[Finding]:
    out: list[Finding] = []
    claims = _Claims()

    def add(kind: str, s: int, e: int, level: Level, claim: bool = True) -> None:
        if claim:
            claims.claim(s, e)
        out.append(Finding(kind, s, e, level))

    for s, e, _ in find_secrets(text):
        add("secret", s, e, L4)
    for m in CANARY.finditer(text):
        add("canary", m.start(), m.end(), L4, claim=False)
    for m in EMAIL.finditer(text):
        if not claims.overlaps(*m.span()):
            add("email", m.start(), m.end(), L3)

    if _DIGIT.search(text):  # every remaining numeric detector needs a digit; most prose has few
        _numeric(text, claims, add)

    persons = names.find_names(text)
    for s, e in persons:
        out.append(Finding("person_name", s, e, L3))
    out.extend(protected.classify_spans(text))

    company_hits = [mm.start() for mm in COMPANY.finditer(text)]  # sorted by construction
    for s, e in _code_blocks(text):
        i = bisect.bisect_left(company_hits, s)
        has_id = i < len(company_hits) and company_hits[i] < e
        out.append(Finding("code", s, e, L3 if has_id else L2))

    if persons:
        out.extend(Finding("performance", m.start(), m.end(), L4) for m in PERFORMANCE.finditer(text))
    if company_hits:
        out.extend(Finding("vulnerability", m.start(), m.end(), L4) for m in VULN.finditer(text))
    if company_hits or MATTER_ID.search(text):
        out.extend(Finding("litigation", m.start(), m.end(), L4) for m in LITIGATION.finditer(text))
    return out
