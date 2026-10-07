"""Result types of the legal services (JSON-ready: `.model_dump(mode="json")`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from corpai.legal.quotes import QuoteSpan

ClauseVerdictKind = Literal["acceptable", "fallback", "unacceptable", "missing"]


class Suggestion(BaseModel):
    """Wording from legal.clause_library (reviewed by the head of legal), never model text."""

    clause_id: str
    clause_type: str
    position: Literal["preferred", "fallback"]
    text: str


class ClauseResult(BaseModel):
    clause_id: str | None  # None: a required term with no clause to attach it to
    heading: str
    topic: str
    verdict: ClauseVerdictKind
    rule: str | None = None  # "KB-01@2026-10-01"
    rules: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"
    confidence: Literal["low", "medium", "high"] = "medium"
    quotes: list[QuoteSpan] = Field(default_factory=list)  # each exists verbatim in the contract
    suggestion: Suggestion | None = None
    suggestion_note: str | None = None
    explanation: str = ""
    embedded_instruction: bool = False
    redline: dict | None = None  # Clause Check's located redline proposal (a proposal for a human only)
    tool_calls: list[str] = Field(default_factory=list)  # the review loop's read-only tool trajectory
    error: str | None = None


class Finding(BaseModel):
    kind: Literal["embedded_instruction"]
    clause_id: str | None
    quote: QuoteSpan
    patterns: list[str] = Field(default_factory=list)
    detected_by: Literal["code", "model", "code+model"] = "code"
    effect: Literal["none"] = "none"  # it was treated as data: no tool call, no verdict change
    note: str = ("Text addressed to an AI system was found in the contract. It was treated as contract data "
                 "and had no effect on the review; a lawyer must read this passage.")


class ReviewResult(BaseModel):
    review_id: str
    contract_id: str | None
    run_id: str
    requested_by: str
    playbook_ref: str
    level: int
    route: Literal["local"] = "local"
    text_sha256: str
    clauses: list[ClauseResult]
    findings: list[Finding] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    stored: bool = False


class DraftClause(BaseModel):
    section: str
    clause_type: str | None
    clause_id: str | None
    position: Literal["preferred", "fallback", "fixed"]


class DraftResult(BaseModel):
    run_id: str
    template_type: str
    title: str
    text: str
    variables: dict
    provided: list[str]
    filled_by_model: list[str]
    missing: list[str]
    clauses: list[DraftClause]
    fallbacks: dict[str, str] = Field(default_factory=dict)  # clause_type -> library clause_id
    playbook_ref: str
    level: int
    model_called: bool = False
    model_output_rejected: str | None = None
