"""Security headers on every response (architecture §14), the Host/Origin check and the session cookie.

Plain ASGI middlewares rather than BaseHTTPMiddleware, so the MCP endpoints' responses pass
through untouched apart from the headers.
"""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlsplit

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


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def host_name(netloc: str) -> str:
    """A Host header or URL netloc -> the lower-case host name, without port or IPv6 brackets."""
    h = netloc.strip().lower()
    if h.startswith("["):
        return h[1:h.index("]")] if "]" in h else ""
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


class HostCheck:
    """DNS rebinding and cross-site writes (issues-log FN-26). A page whose name an attacker rebinds to
    127.0.0.1 is same-origin with this server, so SameSite cookies do not help; but its requests still
    carry the attacker's name in Host. Only loopback names (any port, like the MCP SDK's own check) and
    the configured ones are served. A state-changing request that carries an Origin must come from
    this same host (the UI only calls its own origin)."""

    def __init__(self, app: Any, allowed: Iterable[str] = ()):
        self.app = app
        self.allowed = LOOPBACK_HOSTS | {host_name(h) for h in allowed if h and h.strip()}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from corpai.api.errors import error_response

        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        if host_name(host) not in self.allowed:
            resp = error_response(400, "bad_request", "Unknown host name; open the platform at http://127.0.0.1.")
            return await resp(scope, receive, send)
        origin = headers.get(b"origin")
        if origin is not None and scope.get("method", "GET") not in _SAFE_METHODS:
            if urlsplit(origin.decode("latin-1")).netloc.lower() != host.strip().lower():
                resp = error_response(403, "forbidden", "Requests from another site are refused.")
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


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
