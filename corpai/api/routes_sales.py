"""Sales routes beyond POST /api/runs (M4).

GET /api/sales/accounts?q=  the accounts the caller may see (CRM, RLS), to pick a company name for
the account profile. It calls crm.search_accounts through the MCP client as agent sales_profile,
so the same tool rules, token scope, RLS and audit apply as inside the graph; only the caller's
own accounts come back (a rep sees theirs, the head of sales and the CEO see all).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from corpai.agents import router as app_router
from corpai.api import deps
from corpai.api.errors import ApiError
from corpai.ids import new_id
from corpai.mcp_client import McpCallError
from corpai.types import Principal

router = APIRouter()

APP = "sales_profile"
FIELDS = ("account_id", "name", "status", "industry", "country")


@router.get("/api/sales/accounts")
async def accounts(q: str = Query(default="", max_length=80), p: Principal = Depends(deps.principal),
                   services: Any = Depends(deps.services)) -> dict:
    try:
        route = app_router.resolve(p, APP)
    except app_router.RouterError as e:
        raise ApiError(e.status, e.code, str(e)) from None
    try:
        env = await services.mcp.call(principal=p, agent=route.agent, run_id=new_id("run"), domain="crm",
                                      tool="search_accounts", args={"query": q.strip(), "limit": 50})
    except McpCallError:
        raise ApiError(503, "tool_unavailable", "The CRM could not be reached; try again later.") from None
    rows = env.data.get("accounts") if env.ok and isinstance(env.data, dict) else None
    denied = [{"id": str(d.get("id")), "reason": str(d.get("reason") or "")[:200]}
              for d in env.denied if isinstance(d, dict)]
    return {"accounts": [{k: r.get(k) for k in FIELDS} for r in rows or [] if isinstance(r, dict)],
            "denied": denied, "error": env.error}
