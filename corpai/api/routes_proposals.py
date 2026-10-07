"""Approvals inbox (phase-B spec §18). Deciding goes through corpai.proposals (four-eyes, params
hash re-check, executor); afterwards the requester's waiting thread is resumed as the requester."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from corpai import proposals
from corpai.agents import _runner
from corpai.api import deps
from corpai.api.errors import ApiError, bad_request
from corpai.types import Principal

log = logging.getLogger(__name__)
router = APIRouter()

_CODES = {"bad_request": (400, "bad_request"), "not_found": (404, "not_found"), "forbidden": (403, "forbidden"),
          "conflict": (400, "bad_request"), "expired": (400, "bad_request"),
          "params_hash_mismatch": (400, "bad_request")}


def _api_error(e: proposals.ProposalError) -> ApiError:
    status, code = _CODES.get(getattr(e, "code", ""), (400, "bad_request"))
    return ApiError(status, code, str(e))  # proposal error messages are fixed text


@router.get("/api/proposals")
async def list_proposals(box: str = "mine", p: Principal = Depends(deps.principal)) -> list[dict]:
    if box not in ("mine", "to_approve"):
        raise bad_request("box must be mine or to_approve.")
    return await proposals.list_for(p, box)


class ApproveIn(BaseModel):
    params_hash: str = Field(min_length=1, max_length=128)
    reason: str = Field(max_length=2000)


class RejectIn(BaseModel):
    reason: str = Field(max_length=2000)


async def _resume(p: Principal, prop: Any, request_services: Any, checkpointer: Any) -> dict | None:
    try:
        run = await _runner.resume_for_proposal(p, prop, services=request_services, checkpointer=checkpointer)
    except Exception as e:  # noqa: BLE001 - the decision stands even if the thread cannot continue
        log.warning("resuming the thread of %s failed: %s", prop.id, type(e).__name__)
        return None
    return _runner.for_viewer(run, p, prop.requester_id)


@router.post("/api/proposals/{proposal_id}/approve")
async def approve(proposal_id: str, body: ApproveIn, p: Principal = Depends(deps.principal),
                  services: Any = Depends(deps.services), checkpointer: Any = Depends(deps.checkpointer)) -> dict:
    try:
        prop = await proposals.approve(p, proposal_id, params_hash=body.params_hash, reason=body.reason)
    except proposals.ProposalError as e:
        raise _api_error(e) from None
    run = await _resume(p, prop, services, checkpointer)
    return {"proposal": await proposals.describe(p, prop), "run": run}


@router.post("/api/proposals/{proposal_id}/reject")
async def reject(proposal_id: str, body: RejectIn, p: Principal = Depends(deps.principal),
                 services: Any = Depends(deps.services), checkpointer: Any = Depends(deps.checkpointer)) -> dict:
    try:
        prop = await proposals.reject(p, proposal_id, reason=body.reason)
    except proposals.ProposalError as e:
        raise _api_error(e) from None
    run = await _resume(p, prop, services, checkpointer)
    return {"proposal": await proposals.describe(p, prop), "run": run}
