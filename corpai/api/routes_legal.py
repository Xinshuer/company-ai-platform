"""Legal API beyond POST /api/runs (architecture §13, M2): stored reviews, the contract picker and
contract uploads. Included by create_app() like every corpai/api/routes_<area>.py.

    GET  /api/legal/contracts?status=&limit=      contracts you may see: structured fields, no text
    GET  /api/legal/reviews?contract_id=&limit=   stored reviews you may read (summary only)
    GET  /api/legal/reviews/{review_id}           one stored review in full
    POST /api/legal/uploads {text, filename}      keep a contract file for your next review run

Every route is for people who may start the legal_review app (legal department, CEO); anyone else
gets an explicit 403, and the refusal is audited like any other start denial. Within that, row-level
security decides which rows come back (handling lawyer, head of legal, CEO), so a review or contract
someone may not read answers 404, the same as a missing one. Errors use the §18 shape and fixed
messages: PermissionError -> 403, ValueError -> 400, missing -> 404.

These pages read legal.contracts and legal.reviews directly as the person (principal_tx, RLS), not
through an agent's MCP tools. So that the trail still shows who read which contract list or saved
review (issues-log WR-11), every read writes a metadata-only `retrieval` row: ids, counts and the
highest level, never contract or review text. The contract list is the data of legal.search_contracts,
so it also needs that tool's rule (tool_allowed for the legal_review agent).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from corpai import audit
from corpai.agents.legal import AGENT, uploads
from corpai.api import deps
from corpai.api.errors import bad_request, forbidden, not_found
from corpai.db import pg
from corpai.policy import may_start, tool_allowed
from corpai.types import Principal

router = APIRouter()

_REVIEW_ID = re.compile(r"^rev_[0-9a-z]{8,40}$")
_CONTRACT_ID = re.compile(r"^C-[0-9]{4}$")
_STATUSES = ("draft", "negotiation", "signed", "expired", "terminated")
_CONTRACT_FIELDS = ("contract_id", "account_id", "counterparty", "contract_type", "status", "expires_on",
                    "notice_days", "auto_renew", "handler_id", "version", "level", "trust")


async def _legal_user(p: Principal, action: str) -> None:
    d = may_start(p, AGENT)
    if d.allow:
        return
    await audit.arecord("policy_decision", actor=p.user_id, act_chain=[f"agent:{AGENT}"], action=action,
                        resource=f"agent:{AGENT}", decision="deny", reason=d.rule_id)
    raise forbidden("Only the legal department and the CEO use the legal pages.")


def _iso(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


async def _audit_read(p: Principal, action: str, resource: str, *, level: int | None, details: dict,
                      decision: str = "allow", reason: str = "rls") -> None:
    """One `retrieval` row per read (WR-11). Metadata only; ids here are validated or come from the DB."""
    await audit.arecord("retrieval", actor=p.user_id, action=action, resource=resource, decision=decision,
                        reason=reason, level=level, details=details)


@router.get("/api/legal/contracts")
async def list_contracts(status: str | None = None, limit: int = 100,
                         p: Principal = Depends(deps.principal)) -> dict:
    await _legal_user(p, "legal.list_contracts")
    rule = tool_allowed(p, AGENT, "legal", "search_contracts")
    if not rule.allow:
        await audit.arecord("policy_decision", actor=p.user_id, act_chain=[f"agent:{AGENT}"],
                            action="legal.list_contracts", resource="tool:legal.search_contracts", decision="deny",
                            reason=rule.rule_id)
        raise forbidden("Your role may not list contracts.")
    if status is not None and status not in _STATUSES:
        raise bad_request("status must be one of " + ", ".join(_STATUSES) + ".")
    if not 1 <= limit <= 200:
        raise bad_request("limit must be between 1 and 200.")
    async with pg.principal_tx(p) as conn:  # RLS: only contracts this user may read
        cur = await conn.execute(
            f"SELECT {', '.join(_CONTRACT_FIELDS)} FROM legal.contracts"  # column names are constants
            " WHERE (%s::text IS NULL OR status = %s) ORDER BY contract_id LIMIT %s", (status, status, limit))
        rows = [_iso(dict(r)) for r in await cur.fetchall()]
    await _audit_read(p, "legal.list_contracts", "legal.contracts",
                      level=max((int(r["level"]) for r in rows), default=None),
                      details={"count": len(rows), "status": status, "contract_ids": [r["contract_id"] for r in rows]})
    return {"contracts": rows, "count": len(rows)}


@router.get("/api/legal/reviews")
async def reviews(contract_id: str | None = None, limit: int = 50, p: Principal = Depends(deps.principal)) -> dict:
    from corpai.legal import list_reviews

    await _legal_user(p, "legal.list_reviews")
    if contract_id is not None and not _CONTRACT_ID.fullmatch(contract_id):
        raise bad_request("contract_id must look like C-0004.")
    if not 1 <= limit <= 200:
        raise bad_request("limit must be between 1 and 200.")
    rows = await list_reviews(p, contract_id=contract_id, limit=limit)
    await _audit_read(p, "legal.list_reviews", f"legal.contract:{contract_id}" if contract_id else "legal.reviews",
                      level=max((int(r["level"]) for r in rows), default=None),
                      details={"count": len(rows), "review_ids": [r["review_id"] for r in rows]})
    return {"reviews": [_iso(r) for r in rows], "count": len(rows)}


@router.get("/api/legal/reviews/{review_id}")
async def review(review_id: str, p: Principal = Depends(deps.principal)) -> dict:
    from corpai.legal import get_review

    await _legal_user(p, "legal.get_review")
    if not _REVIEW_ID.fullmatch(review_id):
        raise not_found("No such review among those you may read.")
    found = await get_review(p, review_id)
    if found is None:
        # hidden by RLS and missing are one reason here too
        await _audit_read(p, "legal.get_review", f"legal.review:{review_id}", level=None, details={},
                          decision="deny", reason="not_found_or_not_visible")
        raise not_found("No such review among those you may read.")
    await _audit_read(p, "legal.get_review", f"legal.review:{review_id}", level=found.level,
                      details={"contract_id": found.contract_id, "run_id": found.run_id})
    return found.model_dump(mode="json")


class UploadIn(BaseModel):
    text: str = Field(min_length=1, max_length=uploads.MAX_CHARS)
    filename: str = Field(default="", max_length=200)


@router.post("/api/legal/uploads")
async def upload(body: UploadIn, p: Principal = Depends(deps.principal)) -> dict:
    await _legal_user(p, "legal.upload")
    try:
        up = uploads.put(p.user_id, body.text, body.filename)
    except ValueError as e:  # fixed messages (empty / too long)
        raise bad_request(str(e)) from None
    return {"upload_id": up.upload_id, "chars": len(up.text), "sha256": up.sha256, "filename": up.filename,
            "expires_in_s": uploads.TTL_SECONDS}

