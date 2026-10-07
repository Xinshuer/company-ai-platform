"""hr_screening graph (M4; decision D6; EU AI Act Annex III 4(a); GDPR Art. 22; design-review 二.10).

    screen   corpai.hr.screen_candidate as the HR user: protected attributes and identity removed
             before the model, one tool-less local call per hard requirement, verbatim-quote checks,
             evidence stored (L4). Caps {untrusted_input, sensitive_data}: the résumé is untrusted,
             and this node has no write or outbound capability at all.
    decide   waits for the HR person (LangGraph interrupt {"type": "hr_decision", ...}). Nothing is
             decided by the model: there is no score, ranking or recommendation anywhere.
    record   reports the decision the person made.

The decision itself is a human action: POST /api/hr/screenings/decision calls
corpai.hr.record_decision as the caller (HR on the requisition or the head of HR; a reason is
mandatory) and only then resumes this thread with a value built by code (decision.py). The reason
text stays in hr.screenings (L4); it never enters this graph's state or checkpoints.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from langgraph.graph import END, START, StateGraph

from corpai.agents.runtime import BaseState, RunContext, Step, node
from corpai.hr import DECISIONS, screen_candidate
from corpai.types import Level

APP = "hr_screening"
CANDIDATE_RE = re.compile(r"\bCAND-[A-Za-z0-9]{3,24}\b")
INTERRUPT_TYPE = "hr_decision"

NOTICES = {
    "embedded_instruction": {"zh": "这份简历里藏有针对 AI 的指令：已标记，来自该段落的引文一律不算证据。",
                             "en": "This résumé hides an instruction aimed at AI: flagged, and no quote from that "
                                   "passage counts as evidence."},
    "protected_removed": {"zh": "送入模型前已移除受保护信息（照片、性别、年龄等）和身份信息。",
                          "en": "Protected attributes (photo, gender, age, ...) and identity were removed before "
                                "the model saw the résumé."},
    "no_candidate": {"zh": "请输入候选人编号，例如 CAND-001。", "en": "Enter a candidate id, for example CAND-001."},
}
AI_ACT_NOTE = ("The model only extracts evidence per hard requirement; it gives no score, ranking or "
               "recommendation. A person decides and records a reason (EU AI Act Annex III 4(a), GDPR Art. 22).")


class ScreeningState(BaseState, total=False):
    question: str
    candidate_id: str | None
    evidence: dict | None
    decision: dict | None
    output: dict | None


def initial_state(text: str) -> dict:
    return {"question": text, "candidate_id": None, "evidence": None, "decision": None, "output": None}


def _notice(code: str, **extra: Any) -> dict:
    return {"t": "notice", "code": code, **NOTICES[code], **extra}


def _data(evidence: dict | None, *, candidate_id: str | None, decision: dict | None, awaiting: bool) -> dict:
    ev = evidence or {}
    flags = list(ev.get("flags") or [])
    return {"kind": "hr_screening", "candidate_id": candidate_id, "req_id": ev.get("req_id"),
            "evidence_id": ev.get("evidence_id"), "items": ev.get("items") or [], "flags": flags,
            "protected_removed": [f.split(":", 1)[1] for f in flags if f.startswith("protected_removed:")],
            "embedded_instruction": "embedded_instruction" in flags, "model_route": ev.get("model_route"),
            "level": ev.get("level"), "awaiting_decision": awaiting, "decision": decision, "notice": AI_ACT_NOTE}


@node(caps={"sensitive_data"})
async def intake(state: ScreeningState, runtime: Any) -> dict:
    """Picks the candidate id and raises the session before anything is read or sent: a résumé is
    personal data (L3), and the user's raised level is a floor. Its own node, so the raise is
    checkpointed even when screen fails (for example local_down)."""
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    user_level = Level(max(1, min(4, int(ctx.user_level))))
    if user_level > Level.L1:
        step.raise_to(user_level, "user_level")
    m = CANDIDATE_RE.search(state.get("question") or "")
    if m is None:
        step.event(_notice("no_candidate"))
        return step.update(candidate_id=None, output={"text": NOTICES["no_candidate"]["en"], "citations": [],
                                                      "data": {"kind": "hr_screening", "refused": "no_candidate"}})
    step.raise_to(Level.L3, "hr:resume")
    return step.update(candidate_id=m.group(0), evidence=None, decision=None)


@node(caps={"untrusted_input", "sensitive_data"})
async def screen(state: ScreeningState, runtime: Any) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    cid = state.get("candidate_id") or ""
    ev = await screen_candidate(ctx.principal, candidate_id=cid, run_id=ctx.run_id)  # NoPermission -> forbidden
    evidence = ev.to_json()
    step.event({"t": "route", "route": ev.model_route, "level": int(Level.L3), "reason": "route_by_level"})
    step.raise_to(Level.L4, "hr:screening_evidence")
    if "embedded_instruction" in ev.flags:
        step.event(_notice("embedded_instruction"))
    if any(f.startswith("protected_removed:") for f in ev.flags):
        step.event(_notice("protected_removed"))
    awaiting = bool(ev.evidence_id)  # no hard requirements: nothing was stored, nothing to decide on
    text = (f"Evidence for {cid}: {len(ev.items)} requirement(s) checked. " + AI_ACT_NOTE) if ev.items else \
        f"The requisition of {cid} lists no hard requirements: there is no evidence to review."
    return step.update(candidate_id=cid, evidence=evidence, decision=None,
                       output={"text": text, "citations": [],
                               "data": _data(evidence, candidate_id=cid, decision=None, awaiting=awaiting)})


def valid_resume(value: Any, candidate_id: str | None) -> dict | None:
    """The decision value the API built, or None. Anything else is not a decision."""
    if not (isinstance(value, Mapping) and value.get("type") == INTERRUPT_TYPE
            and value.get("candidate_id") == candidate_id and value.get("decision") in DECISIONS
            and isinstance(value.get("screening_id"), str) and value["screening_id"].startswith("scr_")):
        return None
    return {"decision": value["decision"], "screening_id": value["screening_id"],
            "decided_by": str(value.get("decided_by") or "")}


@node(caps=set())
async def decide(state: ScreeningState, runtime: Any) -> dict:
    from langgraph.types import interrupt

    ev = state.get("evidence") or {}
    value = interrupt({"type": INTERRUPT_TYPE, "candidate_id": state.get("candidate_id"),
                       "evidence_id": ev.get("evidence_id")})
    decision = valid_resume(value, state.get("candidate_id"))
    return {"decision": decision or {"decision": None, "status": "invalid_resume"}}


@node(caps={"sensitive_data"})
async def record(state: ScreeningState, runtime: Any) -> dict:
    d = state.get("decision") or {}
    cid = state.get("candidate_id")
    if d.get("decision"):
        text = f"Decision recorded for {cid}: {d['decision']} ({d['screening_id']}). The reason is stored with it."
    else:
        text = f"No decision was recorded for {cid}."
    return {"output": {"text": text, "citations": [],
                       "data": _data(state.get("evidence"), candidate_id=cid, decision=d or None, awaiting=False)}}


def _after_intake(state: ScreeningState) -> str:
    return "screen" if state.get("candidate_id") else "end"


def _after_screen(state: ScreeningState) -> str:
    ev = state.get("evidence") or {}
    return "decide" if ev.get("evidence_id") else "end"


def build_graph() -> StateGraph:
    g = StateGraph(ScreeningState, context_schema=RunContext)
    g.add_node("intake", intake)
    g.add_node("screen", screen)
    g.add_node("decide", decide)
    g.add_node("record", record)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _after_intake, {"screen": "screen", "end": END})
    g.add_conditional_edges("screen", _after_screen, {"decide": "decide", "end": END})
    g.add_edge("decide", "record")
    g.add_edge("record", END)
    return g
