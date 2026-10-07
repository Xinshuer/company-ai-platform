"""POST /api/runs and GET /api/runs/{run_id}/trace (phase-B spec §18)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from corpai.agents import _runner, router as app_router
from corpai.api import deps
from corpai.api.errors import ApiError, bad_request, not_found
from corpai.trace import read_run
from corpai.types import Level, Principal

router = APIRouter()

MAX_INPUT_CHARS = 4000


class RunIn(BaseModel):
    app: str = Field(min_length=1, max_length=64)
    input: str = Field(max_length=MAX_INPUT_CHARS)
    thread_id: str | None = Field(default=None, max_length=64)
    user_level: int = 1


@router.post("/api/runs")
async def start_run(body: RunIn, p: Principal = Depends(deps.principal), services: Any = Depends(deps.services),
                    checkpointer: Any = Depends(deps.checkpointer)) -> dict:
    text = body.input.strip()
    if not text:
        raise bad_request("Type a question first.")
    if isinstance(body.user_level, bool) or body.user_level not in (1, 2, 3, 4):
        raise bad_request("user_level must be 1, 2, 3 or 4.")
    try:
        return await _runner.start_run(p, app=body.app, text=text, thread_id=body.thread_id,
                                       user_level=Level(body.user_level), services=services,
                                       checkpointer=checkpointer)
    except app_router.RouterError as e:
        raise ApiError(e.status, e.code, str(e)) from None
    except _runner.RunRefused as e:
        raise ApiError(e.status, e.code, e.message) from None


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("/api/runs/{run_id}/trace")
async def run_trace(run_id: str, p: Principal = Depends(deps.principal)) -> dict:
    if not run_id.startswith("run_") or len(run_id) > 64:
        raise not_found("No such run among yours.")
    spans = await read_run(p, run_id)  # RLS: own spans only
    if not spans:
        # someone else's run and a missing one look the same
        raise not_found("No such run among yours.")
    keys = ("span_id", "parent_id", "name", "kind", "level", "status", "started_at", "ended_at", "attrs",
            "input_text", "output_text")
    return {"run_id": run_id, "spans": [{k: _iso(s.get(k)) for k in keys} for s in spans]}
