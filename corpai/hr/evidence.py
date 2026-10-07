"""Screening evidence (decision D6): per hard requirement a verdict and a verbatim quote, nothing more.

There is deliberately no score, rank, recommendation or free-text "comment" field: the schema is
what keeps an instruction hidden in a résumé from travelling anywhere, and what keeps the model
from deciding. A human decides (`decisions.record_decision`) with a reason.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_QUOTE = 400
# A quote is evidence only if it says something: at least this many characters and two words that
# are not redaction placeholders ("e" or "the candidate" are substrings of every résumé).
MIN_QUOTE_CHARS = 12
MIN_QUOTE_WORDS = 2
_PLACEHOLDER = re.compile(r"⟨removed:[^⟩]*⟩|\bthe candidate\b", re.I)


class Verdict(StrEnum):
    MET = "met"
    NOT_MET = "not_met"
    UNKNOWN = "unknown"


class RequirementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str = Field(max_length=40)
    requirement: str = Field(max_length=400)  # the requisition's own text (HR-authored), not model output
    verdict: Verdict
    quote: str | None = Field(default=None, max_length=MAX_QUOTE)
    flags: tuple[str, ...] = ()  # quote_not_verbatim, quote_in_instruction, no_quote, invalid_output, ...


class ScreeningEvidence(BaseModel):
    """What HR sees before deciding. Level L4 (screening results), stored in hr.screening_evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str | None = None
    candidate_id: str
    req_id: str
    items: tuple[RequirementEvidence, ...]
    flags: tuple[str, ...] = ()  # candidate-level: embedded_instruction, protected_removed:<kind>, ...
    model_route: str
    level: int = 4

    def to_json(self) -> dict[str, Any]:
        return json.loads(self.model_dump_json())


def parse_model_verdict(text: str) -> tuple[Verdict, str | None, list[str]]:
    """Untrusted model output → (verdict, quote, flags). Anything off-schema becomes unknown."""
    flags: list[str] = []
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return Verdict.UNKNOWN, None, ["invalid_output"]
    if not isinstance(data, dict):
        return Verdict.UNKNOWN, None, ["invalid_output"]
    extra = sorted(k for k in data if k not in ("verdict", "quote"))
    if extra:
        flags.append("extra_keys_dropped")  # e.g. an injected "score" or "action": never stored
    try:
        verdict = Verdict(str(data.get("verdict", "")).strip().lower())
    except ValueError:
        return Verdict.UNKNOWN, None, [*flags, "invalid_verdict"]
    quote = data.get("quote")
    if quote is not None and not isinstance(quote, str):
        return Verdict.UNKNOWN, None, [*flags, "invalid_quote"]
    quote = quote.strip() if isinstance(quote, str) else None
    return verdict, (quote or None), flags


def check_quote(verdict: Verdict, quote: str | None, resume: str,
                instruction_spans: list[tuple[int, int]]) -> tuple[Verdict, str | None, list[str]]:
    """A met / not_met verdict stands only on a verbatim quote from the (redacted) résumé that does
    not come from an embedded instruction; otherwise it becomes unknown (D6: evidence, not opinion)."""
    if quote is not None and len(quote) > MAX_QUOTE:
        return Verdict.UNKNOWN, None, ["quote_too_long"]
    if verdict is Verdict.UNKNOWN:
        if quote is not None and quote not in resume:
            return Verdict.UNKNOWN, None, ["quote_not_verbatim"]
        return Verdict.UNKNOWN, quote, []
    if not quote:
        return Verdict.UNKNOWN, None, ["no_quote"]
    if not _substantive(quote):
        return Verdict.UNKNOWN, None, ["quote_too_short"]
    pos = resume.find(quote)
    if pos < 0:
        return Verdict.UNKNOWN, None, ["quote_not_verbatim"]
    # Every occurrence must lie outside injected passages; "verified by HR" quoted from the
    # attacker's own sentence is not evidence.
    start = pos
    while start >= 0:
        end = start + len(quote)
        if any(s < end and start < e for s, e in instruction_spans):
            return Verdict.UNKNOWN, None, ["quote_in_instruction"]
        start = resume.find(quote, start + 1)
    return verdict, quote, []


def _substantive(quote: str) -> bool:
    words = re.findall(r"\w+", _PLACEHOLDER.sub(" ", quote))
    return len(quote.strip()) >= MIN_QUOTE_CHARS and len(words) >= MIN_QUOTE_WORDS
