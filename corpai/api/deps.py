"""Request dependencies: the principal behind the session cookie, and app-wide services."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from corpai.api.errors import ApiError, unauthenticated
from corpai.api.security import COOKIE, session_principal
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
