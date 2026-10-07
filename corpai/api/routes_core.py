"""Health, demo login, logout and /api/me (architecture §13, phase-B spec §18)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from corpai import audit
from corpai.api import deps
from corpai.api.errors import ApiError, bad_request, not_found
from corpai.api.security import COOKIE, clear_session_cookie, session_principal, set_session_cookie
from corpai.config import settings
from corpai.identity import directory, tokens
from corpai.policy import apps_for
from corpai.types import Principal, Role

log = logging.getLogger(__name__)
router = APIRouter()

# Same names as the UI's i18n app_<id> keys (ui/js/i18n.js); tests/integration/test_phase_b.py
# compares /api/me with the UI fixtures.
APP_TITLES: dict[str, tuple[str, str]] = {
    "qa": ("知识问答", "Ask"),
    "legal_review": ("合同审查", "Contract review"),
    "report": ("管理层报告", "Management report"),
    "sales_profile": ("客户画像", "Account profile"),
    "hr_headcount": ("编制概览", "Headcount"),
    "hr_screening": ("简历筛查", "Résumé screening"),
}


@router.get("/api/health")
async def health() -> dict:
    from corpai.gateway import ops

    try:
        switches: dict | None = await ops.get_switches()
    except Exception:  # noqa: BLE001 - health must answer even when the database does not
        switches = None
    s = settings()
    return {"ok": True, "mode": s.llm_mode, "sim_today": s.sim_today.isoformat(), "switches": switches}


async def _personas() -> list[dict]:
    try:
        rows = await directory.list_personas()
    except directory.DemoLoginDisabled:
        raise not_found("Demo login is switched off.") from None
    return [{"user_id": r["user_id"], "persona_key": r["persona_key"], "name": r["name"], "title": r["title"],
             "dept": r["dept"], "role": r["role"]} for r in rows]


@router.get("/api/personas")
async def personas() -> list[dict]:
    return await _personas()


class DemoLoginIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


@router.post("/api/auth/demo-login")
async def demo_login(body: DemoLoginIn, request: Request) -> JSONResponse:
    if not settings().demo_login:
        raise not_found("Demo login is switched off.")
    persona = next((p for p in await _personas() if p["user_id"] == body.user_id), None)
    if persona is None:
        # The picker is an identity bypass for the demo: only the listed personas, and every
        # attempt is on record.
        await audit.arecord("demo_login", actor="anonymous", action="login", resource="user:unknown",
                            decision="deny", reason="not_a_persona")
        raise bad_request("Unknown persona; pick one from the list.")
    try:
        token, _sid = await tokens.aissue_session(persona["user_id"])
    except tokens.TokenError:
        await audit.arecord("demo_login", actor=persona["user_id"], action="login", decision="deny",
                            reason="session_refused")
        raise ApiError(403, "forbidden", "This account cannot log in.") from None
    old = await session_principal(request.cookies.get(COOKIE))
    if old is not None:  # switching persona ends the previous session instead of leaving it open
        await tokens.arevoke_session(old[1].sid, old[1].sub)
    await audit.arecord("demo_login", actor=persona["user_id"], action="login", decision="allow",
                        reason="persona_picker", details={"persona_key": persona["persona_key"]})
    resp = JSONResponse({"ok": True, "user_id": persona["user_id"], "persona_key": persona["persona_key"]})
    set_session_cookie(resp, token)
    return resp


@router.post("/api/auth/logout")
async def logout(request: Request) -> JSONResponse:
    found = await session_principal(request.cookies.get(COOKIE))
    if found is not None:
        _p, claims = found
        # sid and user come from the verified cookie: a caller can only end its own session
        await tokens.arevoke_session(claims.sid, claims.sub)
        await audit.arecord("login", actor=claims.sub, action="logout", decision="allow", reason="logout")
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp)
    return resp


async def _persona_key(p: Principal) -> str | None:
    if not settings().demo_login:
        return None
    try:
        rows = await directory.list_personas()
    except directory.DemoLoginDisabled:
        return None
    return next((r["persona_key"] for r in rows if r["user_id"] == p.user_id), None)


def me_payload(p: Principal, persona_key: str | None) -> dict[str, Any]:
    from corpai.agents import router

    # Only apps the user may start AND whose graph exists: a listed app must be runnable.
    apps = [{"id": a, "title_zh": APP_TITLES.get(a, (a, a))[0], "title_en": APP_TITLES.get(a, (a, a))[1]}
            for a in apps_for(p) if router.app_ready(a)]
    return {
        "user_id": p.user_id, "name": p.name, "title": p.title, "dept": str(p.dept), "role": str(p.role),
        "clearance": int(p.clearance), "persona_key": persona_key, "apps": apps,
        "can": {"approve": p.active and p.role in (Role.MANAGER, Role.CEO),
                "audit": p.active and p.role is Role.SECURITY_AUDITOR,
                "admin": p.active and p.role is Role.PLATFORM_ADMIN},
    }


@router.get("/api/me")
async def me(p: Principal = Depends(deps.principal)) -> dict:
    return me_payload(p, await _persona_key(p))
