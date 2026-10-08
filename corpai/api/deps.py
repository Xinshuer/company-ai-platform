"""Request dependencies: the principal behind the session cookie, app-wide services, and the app
start check every milestone route makes first."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from corpai import audit
from corpai.agents import router as app_router
from corpai.api.errors import ApiError, unauthenticated
from corpai.api.security import COOKIE, session_principal
from corpai.policy import may_start
from corpai.types import Principal


async def principal(request: Request) -> Principal:
    """Every request re-resolves the principal; an inactive user or a revoked session gets 401."""
    found = await session_principal(request.cookies.get(COOKIE))
    if found is None:
        raise unauthenticated()
    p, claims = found
    request.state.session_sid = claims.sid
    return p


def services(request: Request) -> Any:
    s = getattr(request.app.state, "services", None)
    if s is None:
        raise ApiError(503, "unavailable", "The platform is starting; try again in a moment.")
    return s


def checkpointer(request: Request) -> Any:
    c = getattr(request.app.state, "checkpointer", None)
    if c is None:
        raise ApiError(503, "unavailable", "The platform is starting; try again in a moment.")
    return c


async def audit_start_denied(p: Principal, app: str, action: str) -> None:
    """The policy_decision deny row of a refused app start (issues-log WR-10): the same row the legal
    pages write, so the auditor finds every "may not start this app" in one place. Only called after
    router.resolve raised StartDenied, so `app` is a known app name, never free request text."""
    agent = app_router.agent_for_app(app)
    if agent is None:
        return
    d = may_start(p, agent)
    await audit.arecord("policy_decision", actor=p.user_id, act_chain=[f"agent:{agent}"], action=action,
                        resource=f"agent:{agent}", decision="deny", reason=d.rule_id)


async def require_app(p: Principal, app: str, *, action: str) -> app_router.AppRoute:
    """router.resolve(p, app) for an API route: an unknown or unready app answers 400, a caller who
    may not start the app 403 `forbidden`, and that refusal is audited."""
    try:
        return app_router.resolve(p, app)
    except app_router.StartDenied as e:
        await audit_start_denied(p, app, action)
        raise ApiError(e.status, e.code, str(e)) from None
    except app_router.RouterError as e:
        raise ApiError(e.status, e.code, str(e)) from None
