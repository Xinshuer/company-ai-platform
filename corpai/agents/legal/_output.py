"""The §18 `output` of each legal_review mode, written by code (never by the model).

`output.text` is a short English summary for generic run cards; the legal page renders
`output.data` (verdicts, quotes, findings, due dates, proposals) itself, as text only.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from corpai.types import Level

_VERDICT_ORDER = ("unacceptable", "missing", "fallback", "acceptable")


def error_output(mode: str, code: str, message: str) -> dict:
    """A refusal or bad input the user can fix, as data (the run itself completed)."""
    return {"text": message, "citations": [], "data": {"mode": mode, "error": {"code": code, "message": message}}}


def _jsonable(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def review_output(review: dict, doc: dict, text: str) -> dict:
    """`review` is ReviewResult.model_dump(mode="json"). The reviewed text goes along so the page
    can highlight the verified quotes at their offsets, except at L4: like an L4 citation, an L4
    contract is shown by its metadata and the per-clause quotes only (BI-02)."""
    summary = review.get("summary") or {}
    counts = summary.get("counts") or {}
    who = doc.get("contract_id") or ("the uploaded file" if doc.get("source") == "upload" else "the pasted text")
    parts = [f"Review of {who} against {review.get('playbook_ref')}: overall risk {summary.get('overall_risk')}."]
    parts.append(", ".join(f"{counts.get(k, 0)} {k}" for k in _VERDICT_ORDER) + ".")
    attention = summary.get("needs_attention") or []
    if attention:
        parts.append("Needs a lawyer's attention: " + ", ".join(str(a) for a in attention[:12]) + ".")
    n = len(review.get("findings") or [])
    if n:
        parts.append(f"{n} passage(s) addressed to an AI system were isolated and had no effect.")
    if summary.get("matches_stored_text") is False:
        parts.append("The text differs from the stored contract (counterparty markup).")
    shown = text if int(review.get("level") or 4) < Level.L4 else None
    document = {**doc, "chars": len(text), "text": shown, "text_withheld": shown is None}
    return {"text": " ".join(parts), "citations": [],
            "data": {"mode": "review", "review": review, "document": document}}


def due_output(month: str, start: date, end: date, today: date, rows: list[dict]) -> dict:
    items = [_jsonable(r) for r in rows]
    if items:
        lines = [f"{r['contract_id']} ({r['counterparty']}, {r['contract_type']}) expires {r['expires_on']}; "
                 f"{r['action']}." for r in items]
        text = f"{len(items)} signed contract(s) you can see expire in {month}: " + " ".join(lines)
    else:
        text = f"No signed contract you can see expires in {month}."
    return {"text": text, "citations": [],
            "data": {"mode": "due", "month": month, "from": start.isoformat(), "to": end.isoformat(),
                     "today": today.isoformat(), "contracts": items}}


def proposal_info(env: Any, tool: str) -> dict:
    return {"proposal_id": env.proposal_id, "tool": f"legal.{tool}", "tier": int(env.tier or 0),
            "status": env.status or "pending"}


def draft_output(draft: dict, account_id: str, proposal: dict | None) -> dict:
    text = f"Draft {draft.get('title')} for account {account_id} assembled from the clause library"
    missing = draft.get("missing") or []
    text += f"; open points: {', '.join(missing)}." if missing else "."
    if proposal:
        text += " Filed as a draft contract proposal (tier 2); confirm it in the approvals inbox."
    return {"text": text, "citations": [],
            "data": {"mode": "draft", "account_id": account_id, "draft": draft, "proposal": proposal}}


def send_output(request: dict, subject: str, proposal: dict) -> dict:
    text = (f"Sending {request['contract_id']} to {request['to']} needs a second person: the head of legal or the "
            f"CEO must approve proposal {proposal['proposal_id']} (tier 3, four-eyes). Nothing has been sent.")
    return {"text": text, "citations": [],
            "data": {"mode": "send", "contract_id": request["contract_id"], "to": request["to"],
                     "subject": subject, "proposal": proposal}}


def finish_output(mode: str, proposal: dict, decision: dict) -> dict:
    """After the approval gate. `approved` is True only when the executor really ran (runtime)."""
    status = str(decision.get("status") or "invalid_resume")
    result = decision.get("result") if isinstance(decision.get("result"), dict) else None
    done = bool(decision.get("approved"))
    if mode == "send":
        text = (f"Sent: outbox entry {result.get('outbox_id')} to {result.get('recipient')}." if done and result
                else f"Not sent (proposal {status}).")
    else:
        text = (f"Draft contract {result.get('contract_id')} created." if done and result
                else f"No draft contract created (proposal {status}).")
    return {"text": text, "citations": [],
            "data": {"mode": mode, "proposal": {**proposal, "status": status}, "executed": done,
                     "result": result}}
