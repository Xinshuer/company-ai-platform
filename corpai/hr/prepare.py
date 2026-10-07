"""Prepare a résumé for the screening model (decision D6).

1. Markup is dropped (styling is not résumé content; a 1px white span is still shown as text).
2. The candidate's name, e-mail and phone are replaced, so two résumés that differ only in the
   name give the model identical input (counterfactual fairness by construction). Name matching
   ignores case and diacritics ("ANNA KOWALCZYK", "anna kowalczyk", "Zoe" for "Zoë") and covers the
   forms names take in handles ("anna.kowalczyk", "annakowalczyk", "a.kowalczyk", "Kowalczyk, Anna",
   "A. Kowalczyk"). A lone first or last name is replaced only in the header (the first lines) and on
   "Name:" lines: elsewhere the same word is usually something else ("Kranich Software GmbH, Berlin"
   for a candidate called Berlin), and replacing it there would make a counterfactual pair differ.
3. URLs and @handles are removed outright (a profile link carries the name in some spelling, and is
   never evidence for a requirement), like any e-mail address: an address in a résumé is never
   evidence, and an attacker's address is exactly what an injection wants the model to repeat.
4. Phone numbers: the known value exactly, plus phone-shaped strings (a leading + or 00, or nine or
   more digits that are not a date range, a thousands-grouped amount or a count followed by a unit).
   "03/2019 - 06/2023" and "40 000 000 stops" stay: durations are evidence for "5 years of ...".
5. `classify.redact_protected` removes photo, gender, age/date of birth, ethnicity, nationality,
   origin, marital status, children, religion, health and disability (EN/DE/ZH, hidden characters too).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from corpai.classify import redact_protected
from corpai.sales.untrusted import has_embedded_instruction, instruction_spans, strip_markup

CANDIDATE = "the candidate"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+"
                  r"|\b(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|me|de|eu|example|page|app|so|co)/[^\s<>\"')\]]*")
_HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,38}[A-Za-z0-9_])?")
_PHONE_CANDIDATE = re.compile(r"(?<![\w/.,])(?:\+|\(\+?)?\d[\d \-()/.]{5,}\d(?![\w/])")
_DATE_IN = re.compile(r"(?<!\d)(?:0?[1-9]|1[0-2])\s*[/.]\s*(?:19|20)\d{2}(?!\d)|(?<!\d)(?:19|20)\d{2}\s*[-–]")
_THOUSANDS = re.compile(r"[1-9]\d{0,2}(?:[ ., ]\d{3})+")
_UNIT_AFTER = re.compile(r"\s*(?:%|€|eur\b|euro|usd|\$|km\b|kilomet|miles?\b|kg\b|tonnes?\b|t\b|stops?\b|"
                         r"vehicles?\b|trucks?\b|users?\b|customers?\b|parcels?\b|deliveries\b|orders?\b|"
                         r"requests?\b|events?\b|records?\b|rows?\b|transactions?\b|messages?\b|lines?\b|"
                         r"hours?\b|days?\b|per\b|a\s+(?:day|month|year)\b)", re.I)
_NAME_LINE = re.compile(r"(?im)^[ \t]*(?:full\s+)?(?:name|candidate|vorname|nachname|姓名)[ \t]*[:：].*$")
_HEADER_LINES = 2  # the name line and the line under it (title or contact line)


@dataclass(frozen=True)
class PreparedResume:
    text: str  # what the model sees
    removed_kinds: tuple[str, ...]  # protected-attribute kinds that were removed (no values)
    embedded_instruction: bool  # flagged on the raw résumé
    instruction_spans: tuple[tuple[int, int], ...]  # in `text`, for the quote check


def _folded(text: str) -> tuple[str, list[int]]:
    """Case- and diacritic-folded copy, with a map from each folded character to its original index."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch >= "\x80" and unicodedata.category(ch) == "Cf":
            continue  # zero-width and other format characters
        for c in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(c):
                continue
            for d in c.casefold():
                out.append(d)
                index.append(i)
    index.append(len(text))
    return "".join(out), index


def _fold(s: str) -> str:
    return _folded(s)[0]


def _replace_spans(text: str, spans: list[tuple[int, int]], repl: str) -> str:
    merged: list[list[int]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    for s, e in reversed(merged):
        text = text[:s] + repl + text[e:]
    return text


def _name_patterns(tokens: list[str]) -> list[re.Pattern[str]]:
    """Patterns over folded text for the whole name in its usual spellings."""
    esc = [re.escape(t) for t in tokens]
    sep = r"[\s._\-]*"
    pats = [sep.join(esc)]  # "anna kowalczyk", "anna.kowalczyk", "annakowalczyk", "ANNA\nKOWALCZYK"
    if len(tokens) >= 2:
        last, firsts = esc[-1], esc[:-1]
        pats.append(last + r"\s*,\s*" + r"\s+".join(firsts))  # "Kowalczyk, Anna"
        pats.append(last + r"[._\-]?" + r"[._\-]?".join(firsts))  # "kowalczyk.anna"
        initials = r"\.?[\s._\-]*".join(re.escape(t[0]) for t in tokens[:-1] if t)
        pats.append(initials + r"\.?[\s._\-]*" + last)  # "A. Kowalczyk", "a.kowalczyk", "akowalczyk"
    return [re.compile(rf"(?<![\w]){p}(?![\w])") for p in pats]


def _strip_name(text: str, name: str) -> str:
    tokens = [t for t in _fold(" ".join(str(name or "").split())).split(" ") if t]
    if not tokens:
        return text
    folded, index = _folded(text)
    spans = [(index[m.start()], index[m.end() - 1] + 1)
             for pat in _name_patterns(tokens) for m in pat.finditer(folded)]
    text = _replace_spans(text, spans, CANDIDATE)
    # Lone first or last names: only where they are the candidate's (header lines, "Name:" lines).
    lone = [t for t in sorted(set(tokens), key=len, reverse=True) if len(t) >= 3]
    if not lone:
        return text
    zones: list[tuple[int, int]] = []
    pos, seen = 0, 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            zones.append((pos, pos + len(line)))
            seen += 1
        pos += len(line)
        if seen >= _HEADER_LINES:
            break
    zones += [m.span() for m in _NAME_LINE.finditer(text)]
    folded, index = _folded(text)
    pat = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(t) for t in lone) + r")(?![\w-])")
    spans = []
    for m in pat.finditer(folded):
        s, e = index[m.start()], index[m.end() - 1] + 1
        if any(zs <= s and e <= ze for zs, ze in zones):
            spans.append((s, e))
    return _replace_spans(text, spans, CANDIDATE)


def _phone_sub(m: re.Match[str]) -> str:
    s = m.group()
    digits = sum(ch.isdigit() for ch in s)
    international = s.lstrip("(").startswith(("+", "00"))
    if digits < (8 if international else 9):
        return s
    if not international:
        if _DATE_IN.search(s) or _THOUSANDS.fullmatch(s.strip()):
            return s  # "03/2019 - 06/2023", "40 000 000"
        if _UNIT_AFTER.match(m.string, m.end()):
            return s  # "120 000 000 stops per year"
    return "⟨removed:phone⟩"


def prepare_resume(resume_text: str, *, name: str, email: str | None = None,
                   phone: str | None = None) -> PreparedResume:
    raw = str(resume_text or "")
    flagged = has_embedded_instruction(raw)
    text = strip_markup(raw)
    for value in (email, phone):
        if value:
            text = text.replace(value, "⟨removed:contact⟩")
    text = _EMAIL.sub("⟨removed:email⟩", text)
    text = _URL.sub("⟨removed:link⟩", text)
    text = _HANDLE.sub("⟨removed:handle⟩", text)
    text = _strip_name(text, name)
    text = _PHONE_CANDIDATE.sub(_phone_sub, text)
    redacted, findings = redact_protected(text)
    kinds = tuple(sorted({f.kind for f in findings}))
    return PreparedResume(text=redacted, removed_kinds=kinds, embedded_instruction=flagged,
                          instruction_spans=tuple(instruction_spans(redacted)))
