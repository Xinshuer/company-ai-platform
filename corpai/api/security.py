"""Security headers on every response (architecture §14) and the session cookie.

A plain ASGI middleware rather than BaseHTTPMiddleware, so the MCP endpoints' responses pass
through untouched apart from the headers.
"""

from __future__ import annotations

from typing import Any

from corpai.identity import directory, tokens
from corpai.types import Principal

CSP = ("default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self' http://127.0.0.1:8000")
HEADERS: list[tuple[bytes, bytes]] = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
]

COOKIE = "corpai_session"
SESSION_MAX_AGE = 8 * 3600  # equals the session token lifetime (identity.tokens)


class SecurityHeaders:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        api = scope.get("path", "").startswith("/api/")

        async def wrapped(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in {h for h, _ in HEADERS}]
                headers.extend(HEADERS)
                if api:  # personal, possibly confidential answers: never cached by the browser
                    headers.append((b"cache-control", b"no-store"))
                message = dict(message, headers=headers)
            await send(message)

        return await self.app(scope, receive, wrapped)


def set_session_cookie(response: Any, token: str) -> None:
    # Not `secure`: the demo is served over http://127.0.0.1. HttpOnly keeps the token away from
    # page scripts; SameSite=Strict keeps other sites from riding on it (no CSRF token needed).
    response.set_cookie(COOKIE, token, max_age=SESSION_MAX_AGE, path="/", httponly=True, samesite="strict")


def clear_session_cookie(response: Any) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")


async def session_principal(cookie: str | None) -> tuple[Principal, tokens.SessionClaims] | None:
    """Verify the cookie (signature, expiry, open session, active user) and re-read the principal
    from the directory: deactivation and revoked grants take effect on the very next request."""
    if not cookie:
        return None
    try:
        claims = await tokens.averify_session(cookie)
    except tokens.TokenError:
        return None
    p = await directory.get_principal(claims.sub)
    if p is None or not p.active or p.user_id != claims.sub:
        return None
    return p, claims
