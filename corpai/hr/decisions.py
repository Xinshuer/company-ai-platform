"""The human screening decision (GDPR Art. 22: meaningful human involvement; D6: HR decides).

`record_decision` is called by the API route behind the HR user's own button press, never by a
graph node or a model: it is not in the MCP tool catalogue and no agent holds it. The database
function checks the actor again (HR staff on the requisition or the head of HR) and refuses a
decision without a reason; every attempt is audited, refusals included.
"""

from __future__ import annotations

import psycopg

from corpai import audit
from corpai.db import pg
from corpai.hr.errors import NoPermission
from corpai.ids import new_id
from corpai.types import Principal

DECISIONS = ("advance", "reject", "hold")
MAX_REASON = 2000


async def _audit(principal: Principal, candidate_id: str, decision: str, allow: bool, why: str,
                 screening_id: str | None) -> None:
    # Metadata only: the reason may describe the candidate, so its text stays in hr.screenings (L4).
    await audit.arecord("tool_call", actor=principal.user_id, act_chain=["app:hr_screening"],
                        action="hr.record_decision", resource=f"hr.candidate:{candidate_id}",
                        decision="allow" if allow else "deny", reason=why, level=4,
                        details={"decision": decision if decision in DECISIONS else "invalid",
                                 "screening_id": screening_id})


async def record_decision(principal: Principal, *, candidate_id: str, decision: str, reason: str) -> str:
    """Store HR's decision (advance | reject | hold) with a non-empty reason. Returns the screening id."""
    if decision not in DECISIONS:
        await _audit(principal, candidate_id, str(decision)[:20], False, "invalid_decision", None)
        raise ValueError("decision must be advance, reject or hold")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON:
        await _audit(principal, candidate_id, decision, False, "reason_required", None)
        raise ValueError("a decision needs a reason: say why, in your own words (up to 2000 characters)")
    screening_id = new_id("scr")
    try:
        async with pg.principal_tx(principal) as conn:
            await conn.execute("SELECT hr.record_screening_decision(%s, %s, %s, %s)",
                               (screening_id, candidate_id, decision, reason))
    except psycopg.errors.InsufficientPrivilege:
        await _audit(principal, candidate_id, decision, False, "no_permission", None)
        raise NoPermission("no permission to decide on this candidate (or it does not exist)") from None
    except psycopg.errors.InvalidParameterValue:
        await _audit(principal, candidate_id, decision, False, "invalid_parameter", None)
        raise ValueError("decision must be advance, reject or hold, with a reason") from None
    await _audit(principal, candidate_id, decision, True, "human_decision", screening_id)
    return screening_id
