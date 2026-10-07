"""Helpers for untrusted text (web pages, résumés): spot embedded instructions, strip markup.

Shared by the M4 sales and HR modules. Detection is a *flag* for humans and for quote checks, not
the defence: the defence is that untrusted text only reaches model calls with no tools whose output
is validated against a fixed schema (design-review 二.6, ENGINEERING rule 8). A detector that
misses a phrasing therefore costs a missing flag, never an action.
"""

from __future__ import annotations

import re

# Phrases that address an AI system or ask for actions. Case-insensitive; deliberately broad.
_INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:ignore|disregard|forget)\b[^\n]{0,40}\b(?:instructions?|requirements?|rules?|prompt)\b",
    r"\b(?:instructions?|note|notes|message|verification note)\s+(?:for|to)\s+(?:the\s+|any\s+)?"
    r"(?:ai|llm|automated|assistant|model|screening assistant|research tools?|agents?)\b",
    r"\b(?:ai|llm)\s+(?:agent|assistant|system|systems|reviewer|reviewers|tool|tools)\s*[:,]",
    r"\b(?:ai|llm)\s+assistants?\s+reading\b",
    r"\b(?:crm|hris|legal|web|code|finance|docs)\.[a-z_]{3,40}\s*\(",   # tool-call syntax
    r"\b(?:call|use)\s+(?:the\s+)?(?:crm|hris|legal|web|code|finance|docs)\.[a-z_]{3,40}\b",
    r"\b(?:send|forward|email|mail)\b[^\n]{0,80}@[a-z0-9.-]+\.[a-z]{2,}",  # exfiltration to an address
    r"\bdo not (?:mention|tell|reveal)\b",
    r"\bmark every requirement\b",
))

_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?(?:</\1\s*>|$)", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"</?[A-Za-z][^>\n]{0,500}>")


def instruction_spans(text: str) -> list[tuple[int, int]]:
    """Spans of the *lines* (or HTML comment / markup blocks) that carry instruction-like phrases.

    Whole lines are returned so a quote check can refuse any quote taken from an injected passage,
    including harmless-looking words inside it ("verified by HR").
    """
    if not isinstance(text, str) or not text:
        return []
    hits: list[tuple[int, int]] = []
    for pat in _INSTRUCTION_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.span())
    if not hits:
        return []
    blocks = [m.span() for m in _HTML_COMMENT.finditer(text)]
    blocks += [m.span() for m in re.finditer(r"<span\b[^>]*>.*?(?:</span>|$)", text, re.DOTALL | re.IGNORECASE)]
    out: list[tuple[int, int]] = []
    for s, e in hits:
        block = next(((bs, be) for bs, be in blocks if bs <= s and e <= be), None)
        if block is None:
            ls = text.rfind("\n", 0, s) + 1
            le = text.find("\n", e)
            block = (ls, len(text) if le < 0 else le)
        out.append(block)
    out.sort()
    merged: list[tuple[int, int]] = []
    for s, e in out:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def has_embedded_instruction(text: str) -> bool:
    return bool(instruction_spans(text))


def strip_markup(text: str) -> str:
    """Drop HTML comments, script/style blocks and tags (keeping tag *content*).

    Comments and scripts are not part of what a reader of the published page sees, so removing them
    loses no public fact. Visible text, including any visible injection, is kept: the schema is
    what neutralises it, and hiding it would also hide it from the flag.
    """
    text = _HTML_COMMENT.sub(" ", text)
    text = _SCRIPT_STYLE.sub(" ", text)
    return _TAG.sub("", text)
