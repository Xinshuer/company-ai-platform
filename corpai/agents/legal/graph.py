"""The legal_review graph (architecture §12/§16 M2, design-review 二.6 and 七 M2).

One app, four modes, picked by code from the parsed input (corpai/agents/legal/_input.py):

    review   intake -> review                                     untrusted_input + sensitive_data
    due      due                                                  sensitive_data (SQL, no model)
    draft    draft -> propose_draft -> approval -> finish         sensitive_data, then a tier-2 proposal
    send     propose_send -> approval -> finish                   sensitive_data, then a tier-3 proposal

Rule of Two. The counterparty's contract (stored negotiation paper, an upload, pasted text) only
enters `intake` and `review`, which hold no write tool and nothing outbound: `intake` makes one
read call (legal.get_contract, with a contract id the lawyer typed, checked by code) and `review`
calls corpai.legal.review_contract, whose model loop only has Clause Check's read-only scratch
lookups. No path joins them to a write: the review never files anything.

Sending is a separate run the lawyer starts on purpose (the page's "send to counterparty"
button). Its state holds no contract text or review output: initial_state resets every run-scoped
key, the request carries only the contract id, the recipient and the lawyer's own note, and the
tool arguments are built by code from those. The MCP server checks the lawyer may propose on the
contract, files a tier-3 proposal, and the graph waits in `approval` until a second person (head of
legal or CEO; four-eyes) decides. The executor re-checks that the recipient is a CRM contact of the
contract's account. Drafting works the same way with a tier-2 proposal the lawyer may confirm.

Routing is the gateway's: every legal model call carries hwm >= L3, so it is local.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, START, StateGraph

import corpai.legal  # noqa: F401  registers the legal FakeLLM responders (fake mode)
from corpai import clock
from corpai.agents.legal import _input, _output, uploads
from corpai.agents.runtime import BaseState, RunContext, Step, approval_gate, node
from corpai.classify import classify_text
from corpai.legal import (
    DRAFT_CAPS,
    DUE_CAPS,
    REVIEW_CAPS,
    DraftRejected,
    contracts_due,
    draft_contract,
    month_bounds,
    review_contract,
)
from corpai.types import Level

AGENT = "legal_review"
APP = "legal_review"
DEFAULT_SUBJECT = "Contract {cid} from ABC Tech AB"
DEFAULT_BODY = "Please find the contract attached. Kind regards, ABC Tech Legal"
# send_contract and create_contract_draft: the only write tools this graph files, each from its own
# node on a path without untrusted input, followed by the approval gate.
SEND_CAPS = frozenset({"sensitive_data", "external_action"})
INTAKE_CAPS = REVIEW_CAPS

_RUN_KEYS = ("request", "input_error", "contract_text", "doc", "review", "draft", "proposal", "decision", "output")


class LegalState(BaseState, total=False):
    request: dict  # the parsed input; for review the contract text is kept apart in contract_text
    input_error: str | None
    contract_text: str
    doc: dict | None
    review: dict | None
    draft: dict | None
    proposal: dict | None
    decision: dict | None
    output: dict | None


def initial_state(text: str) -> dict:
    """The per-run input. Every run-scoped key is reset, so a send run never sees the contract text
    or the review of an earlier run in the same thread (hwm and events carry over by design)."""
    out: dict[str, Any] = {k: None for k in _RUN_KEYS}
    out["contract_text"] = ""
    try:
        req = _input.parse(text)
    except _input.InputError as e:
        out["request"] = {"mode": "invalid"}
        out["input_error"] = str(e)
        return out
    if req["mode"] == "review":
        out["contract_text"] = req.pop("text") or ""
    out["request"] = req
    return out


def _enter(step: Step, ctx: RunContext, text: str) -> None:
    """§12: the first node of a run raises hwm to max(classify_text(input), the user's level)."""
    c = classify_text(text or "")
    user_level = Level(max(1, min(4, int(ctx.user_level))))
    step.event({"t": "classify", "level": int(c.level), "findings": sorted({f.kind for f in c.findings})})
    step.raise_to(max(c.level, user_level), "input" if c.level >= user_level else "user_level")


def _tool_error(env: Any, what: str) -> tuple[str, str]:
    # Denials are explicit (ENGINEERING rule 6); the MCP messages are fixed texts, never row content.
    if env.error in ("tool_denied", "not_found_or_denied"):
        return "forbidden", f"{what}: not found, or you have no permission for it."
    if env.error == "bad_arguments" and env.message:
        return "bad_request", f"{what}: {str(env.message)[:200]}"
    return "tool_unavailable", f"{what}: the legal system could not be reached; try again."


# ---- review ---------------------------------------------------------------------------------

@node(caps=INTAKE_CAPS)
async def intake(state: LegalState, runtime) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    req = state["request"]
    cid = req.get("contract_id")
    text = state.get("contract_text") or ""
    doc: dict[str, Any] = {"contract_id": cid, "filename": None}
    if req.get("upload_id"):
        up = uploads.get(ctx.principal.user_id, req["upload_id"])
        if up is None:
            return step.update(output=_output.error_output(
                "review", "not_found", "The upload is gone or not yours; upload the file again."), contract_text="")
        text, doc["source"], doc["filename"] = up.text, "upload", up.filename or None
    elif text:
        doc["source"] = "pasted"
    else:
        env = await step.call_tool("legal", "get_contract", {"contract_id": cid})
        contract = (env.data or {}).get("contract") if env.ok and isinstance(env.data, dict) else None
        if not contract or not isinstance(contract.get("text"), str):
            code, msg = _tool_error(env, f"Contract {cid}")
            return step.update(output=_output.error_output("review", code, msg), contract_text="")
        text, doc["source"] = contract["text"], "stored"
        doc.update({"counterparty": contract.get("counterparty"), "contract_type": contract.get("contract_type"),
                    "status": contract.get("status"), "version": contract.get("version"),
                    "trust": str(env.trust)})
    _enter(step, ctx, text)
    return step.update(contract_text=text, doc=doc)


@node(caps=REVIEW_CAPS)
async def review(state: LegalState, runtime) -> dict:
    """The isolated review: no MCP tool, no write, nothing outbound. The contract is data; what it
    says to an AI is reported as a finding with effect none."""
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    req, text = state["request"], state.get("contract_text") or ""
    try:
        result = await review_contract(ctx.principal, text=text, contract_id=req.get("contract_id"),
                                       run_id=ctx.run_id, gateway=ctx.services.gateway)
    except ValueError as e:  # input limits (size, clause count): fixed messages, no contract text
        return step.update(output=_output.error_output("review", "bad_request", str(e)[:200]), contract_text="")
    step.raise_to(result.level, "legal_review")
    summary = result.summary
    seqs = summary.get("audit_seqs") or []
    for route in summary.get("routes") or []:
        step.event({"t": "route", "route": route, "level": int(result.level), "reason": "route_by_level",
                    "calls": int(summary.get("model_calls") or 0), "audit_seqs": seqs[:200]})
    for f in result.findings:
        where = f"clause {f.clause_id}" if f.clause_id else "the contract"
        where_zh = f"条款 {f.clause_id}" if f.clause_id else "合同"
        step.event({"t": "notice", "code": "embedded_instruction", "clause_id": f.clause_id, "effect": "none",
                    "detected_by": f.detected_by,
                    "zh": f"{where_zh}中有写给 AI 的指令：已隔离，无任何效果，请律师阅读该段。",
                    "en": f"Text addressed to an AI in {where} was isolated and had no effect; a lawyer must read it."})
    if summary.get("errors"):
        step.event({"t": "notice", "code": "clause_errors", "zh": "部分条款未能审查，需要人工处理。",
                    "en": "Some clauses could not be reviewed and need a lawyer."})
    data = result.model_dump(mode="json")
    doc = dict(state.get("doc") or {})
    return step.update(review=data, contract_text="", output=_output.review_output(data, doc, text))


# ---- due ------------------------------------------------------------------------------------

@node(caps=DUE_CAPS)
async def due(state: LegalState, runtime) -> dict:
    """'Which contracts expire next month' is SQL (RLS as the user), not a model reading documents."""
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    month = state["request"].get("month")
    _enter(step, ctx, month or "")
    try:
        start, end = month_bounds(month)
        rows = await contracts_due(ctx.principal, month=month, run_id=ctx.run_id)
    except ValueError as e:
        return step.update(output=_output.error_output("due", "bad_request", str(e)[:200]))
    level = max((int(r.get("level") or Level.L4) for r in rows), default=int(Level.L1))
    step.event({"t": "tool", "domain": "legal", "tool": "contracts_due", "decision": "allow", "level": level,
                "ok": True, "denied": [], "via": "sql"})
    if rows:
        step.raise_to(level, "tool:legal.contracts_due")
    label = month or f"{start.year:04d}-{start.month:02d}"
    return step.update(output=_output.due_output(label, start, end, clock.today(), rows))


# ---- draft ----------------------------------------------------------------------------------

@node(caps=DRAFT_CAPS)
async def draft(state: LegalState, runtime) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    req = state["request"]
    _enter(step, ctx, req.get("instructions", "") + " " + json.dumps(req.get("variables") or {}, ensure_ascii=False))
    try:
        result = await draft_contract(ctx.principal, template_type=req["template_type"],
                                      variables=req.get("variables") or {}, run_id=ctx.run_id,
                                      instructions=req.get("instructions") or "", gateway=ctx.services.gateway)
    except (DraftRejected, ValueError) as e:
        return step.update(output=_output.error_output("draft", "bad_request", str(e)[:200]))
    step.raise_to(result.level, "legal_draft")
    if result.model_called:
        # draft_contract called the gateway with hwm >= L3 and it answered: that is the local route
        step.event({"t": "route", "route": "local", "level": int(result.level), "reason": "route_by_level"})
    return step.update(draft=result.model_dump(mode="json"))


@node(caps=SEND_CAPS)
async def propose_draft(state: LegalState, runtime) -> dict:
    """Files the code-assembled draft unchanged as a tier-2 proposal; nothing is written yet."""
    step = Step(runtime, state)
    req, d = state["request"], state["draft"]
    env = await step.call_tool("legal", "create_contract_draft",
                               {"account_id": req["account_id"], "contract_type": d["template_type"], "text": d["text"]})
    if not (env.ok and env.proposal_id):
        code, msg = _tool_error(env, f"A draft for {req['account_id']}")
        out = _output.draft_output(d, req["account_id"], None)
        out["data"]["error"] = {"code": code, "message": msg}
        return step.update(output=out)
    prop = _output.proposal_info(env, "create_contract_draft")
    return step.update(proposal=prop, output=_output.draft_output(d, req["account_id"], prop))


# ---- send -----------------------------------------------------------------------------------

@node(caps=SEND_CAPS)
async def propose_send(state: LegalState, runtime) -> dict:
    """Arguments from the lawyer's request only (contract id, recipient, own note), never from a
    review. Files a tier-3 proposal; another person must approve before anything leaves."""
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    req = state["request"]
    _enter(step, ctx, req.get("note") or "")
    subject = DEFAULT_SUBJECT.format(cid=req["contract_id"])
    args = {"contract_id": req["contract_id"], "to": req["to"], "subject": subject,
            "body": req.get("note") or DEFAULT_BODY}
    env = await step.call_tool("legal", "send_contract", args)
    if not (env.ok and env.proposal_id):
        code, msg = _tool_error(env, f"Sending {req['contract_id']}")
        return step.update(output=_output.error_output("send", code, msg))
    prop = _output.proposal_info(env, "send_contract")
    return step.update(proposal=prop, output=_output.send_output(req, subject, prop))


# ---- approval and result ----------------------------------------------------------------------

@node(caps=set(), gate=True)
async def approval(state: LegalState, runtime) -> dict:
    return {"decision": await approval_gate(runtime, state["proposal"]["proposal_id"])}


@node(caps={"sensitive_data"})
async def finish(state: LegalState, runtime) -> dict:
    step = Step(runtime, state)
    mode = state["request"]["mode"]
    out = _output.finish_output(mode, state["proposal"], state.get("decision") or {})
    if mode == "draft" and state.get("draft"):
        out["data"]["draft"] = state["draft"]
    return step.update(output=out)


@node(caps=set())
async def invalid(state: LegalState, runtime) -> dict:
    msg = state.get("input_error") or _input.HELP
    return {"output": _output.error_output("invalid", "bad_request", msg)}


# ---- wiring ---------------------------------------------------------------------------------

_ENTRY = {"review": "intake", "due": "due", "draft": "draft", "send": "propose_send", "invalid": "invalid"}


def _pick_mode(state: LegalState) -> str:
    return _ENTRY.get((state.get("request") or {}).get("mode"), "invalid")


def _continue_unless_answered(nxt: str):
    """A node that already wrote `output` refused the request (bad input, denial): stop there."""
    def pick(state: LegalState) -> str:
        return END if state.get("output") is not None else nxt
    return pick


def _to_gate(state: LegalState) -> str:
    return "approval" if (state.get("proposal") or {}).get("proposal_id") else END


def build_graph() -> StateGraph:
    g = StateGraph(LegalState, context_schema=RunContext)
    for name, fn in (("intake", intake), ("review", review), ("due", due), ("draft", draft),
                     ("propose_draft", propose_draft), ("propose_send", propose_send), ("approval", approval),
                     ("finish", finish), ("invalid", invalid)):
        g.add_node(name, fn)
    g.add_conditional_edges(START, _pick_mode, {v: v for v in _ENTRY.values()})
    g.add_conditional_edges("intake", _continue_unless_answered("review"), {"review": "review", END: END})
    g.add_edge("review", END)
    g.add_edge("due", END)
    g.add_edge("invalid", END)
    g.add_conditional_edges("draft", _continue_unless_answered("propose_draft"),
                            {"propose_draft": "propose_draft", END: END})
    g.add_conditional_edges("propose_draft", _to_gate, {"approval": "approval", END: END})
    g.add_conditional_edges("propose_send", _to_gate, {"approval": "approval", END: END})
    g.add_edge("approval", "finish")
    g.add_edge("finish", END)
    return g
