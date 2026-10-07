"""HR routes beyond POST /api/runs (M4): candidate lists for hiring managers and HR, one candidate's
résumé, and the HR person's screening decision.

- Reads go through the MCP client as the app's agent (hr_candidates or hr_screening), so the user's
  tool rules ∩ the agent's allowlist ∩ the one-tool token ∩ RLS apply exactly as inside a graph,
  and the tool servers audit every call. The caller must be allowed to start that app first.
- The decision is the person's own action (corpai/agents/hr/decision.py): never a model's.
Errors follow §18: NoPermission -> 403 forbidden, ValueError -> 400 bad_request.
"""

from __future__ import annotations

import functools
import re
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from corpai.agents import _runner, router as app_router
from corpai.agents.hr import DecisionRefused, collect_candidates, decide
from corpai.api import deps
from corpai.api.errors import ApiError, bad_request, forbidden, not_found
from corpai.hr import NoPermission
from corpai.ids import new_id
from corpai.mcp_client import McpCallError
from corpai.types import Principal

router = APIRouter()

READ_APPS = ("hr_candidates", "hr_screening")
_REQ = re.compile(r"^R-[0-9]{2}$")
_CAND = re.compile(r"^CAND-[0-9]{3,6}$")
CANDIDATE_FIELDS = ("candidate_id", "req_id", "name", "stage", "applied_on", "source", "resume_text")


def _route_for(p: Principal, app: str) -> Any:
    if app not in READ_APPS:
        raise bad_request("app must be hr_candidates or hr_screening.")
    try:
        return app_router.resolve(p, app)
    except app_router.RouterError as e:
        raise ApiError(e.status, e.code, str(e)) from None


def _tool_call(services: Any, p: Principal, agent: str):
    # one run id for the request, so the tool servers' audit rows of this page view belong together
    return functools.partial(_call, services, p, agent, new_id("run"))


async def _call(services: Any, p: Principal, agent: str, run_id: str, domain: str, tool: str, args: dict) -> Any:
    return await services.mcp.call(principal=p, agent=agent, run_id=run_id, domain=domain, tool=tool, args=args)


@router.get("/api/hr/candidates")
async def candidates(app: str = "hr_candidates", req_id: str | None = None, p: Principal = Depends(deps.principal),
                     services: Any = Depends(deps.services)) -> dict:
    route = _route_for(p, app)
    if req_id is not None and not _REQ.match(req_id):
        raise bad_request("req_id looks like R-01.")
    try:
        data = await collect_candidates(p, route.agent, _tool_call(services, p, route.agent),
                                        requested=[req_id] if req_id else [])
    except McpCallError:
        raise ApiError(503, "tool_unavailable", "The HR system could not be reached; try again later.") from None
    return {"app": app, **data}


@router.get("/api/hr/candidates/{candidate_id}")
async def candidate(candidate_id: str, app: str = "hr_candidates", p: Principal = Depends(deps.principal),
                    services: Any = Depends(deps.services)) -> dict:
    route = _route_for(p, app)
    if not _CAND.match(candidate_id):
        raise not_found("No such candidate, or you have no permission to see them.")
    try:
        env = await _tool_call(services, p, route.agent)("hris", "get_candidate", {"candidate_id": candidate_id})
    except McpCallError:
        raise ApiError(503, "tool_unavailable", "The HR system could not be reached; try again later.") from None
    cand = env.data.get("candidate") if env.ok and isinstance(env.data, dict) else None
    if env.error == "tool_denied":
        raise forbidden("Your role may not open candidates through this app.")
    if not isinstance(cand, dict):
        # hidden by RLS and missing look the same
        raise not_found("No such candidate, or you have no permission to see them.")
    # The résumé is untrusted text (trust travels with it): the page shows it as text, never markup.
    return {"candidate": {k: cand.get(k) for k in CANDIDATE_FIELDS}, "level": int(env.level),
            "trust": str(env.trust)}


class DecisionIn(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(min_length=1, max_length=64)
    decision: str = Field(min_length=1, max_length=20)
    reason: str = Field(max_length=2000)


@router.post("/api/hr/screenings/decision")
async def screening_decision(body: DecisionIn, p: Principal = Depends(deps.principal),
                             services: Any = Depends(deps.services),
                             checkpointer: Any = Depends(deps.checkpointer)) -> dict:
    try:
        return await decide(p, thread_id=body.thread_id, candidate_id=body.candidate_id, decision=body.decision,
                            reason=body.reason, services=services, checkpointer=checkpointer)
    except DecisionRefused as e:
        raise ApiError(e.status, e.code, e.message) from None
    except app_router.RouterError as e:
        raise ApiError(e.status, e.code, str(e)) from None
    except _runner.RunRefused as e:
        raise ApiError(e.status, e.code, e.message) from None
    except NoPermission:
        raise forbidden("You may not decide on this candidate (HR on the requisition or the head of HR only).") \
            from None
    except ValueError as e:
        # corpai.hr.record_decision's messages are fixed text ("a decision needs a reason: ...")
        raise bad_request(str(e)) from None
