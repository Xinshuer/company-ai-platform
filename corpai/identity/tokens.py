"""Session, tool and gateway tokens (architecture §5, design-review 二.4).

- Session token: what the browser cookie holds. HS256 over session.key; claims sub, sid, iat, exp
  (8 h). The sid must also be open in core.sessions, so logout / revocation works before exp.
- Tool token: minted per MCP call, aud="mcp:<domain>", sub = the human, act = the agent, scope = the
  tool names; 60 s by default; tool.key.
- Gateway token: Bearer key for the OpenAI-compatible endpoint, aud="gateway", carries the
  sensitivity floor; gateway.key.

Token text never appears in exception messages, reprs or logs: every error below is raised with a
fixed message and `from None`, so not even the PyJWT exception (whose text we do not control)
travels with it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

import jwt

from corpai.identity import _db, keys
from corpai.ids import new_id
from corpai.types import Level, Principal, Role

ISSUER = "corpai"
ALGORITHM = "HS256"
LEEWAY_S = 5
SESSION_TTL_S = 8 * 3600
MAX_TOOL_TTL_S = 600  # a tool token outliving one call by minutes is already generous
MAX_GATEWAY_TTL_S = 3600
_MAX_TOKEN_LEN = 8192
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")  # domains, agents, tool names


# ---- errors -------------------------------------------------------------------------------

class TokenError(Exception):
    """Base class. Messages are fixed strings and never contain token text."""


class TokenExpired(TokenError):
    pass


class WrongAudience(TokenError):
    pass


class BadSignature(TokenError):
    pass


class NotInScope(TokenError):
    pass


class SessionRevoked(TokenError):
    pass


# ---- claims -------------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionClaims:
    sub: str
    sid: str
    iat: int
    exp: int


@dataclass(frozen=True)
class ToolClaims:
    sub: str  # the human user id
    agent: str  # agent name without the "agent:" prefix
    domain: str
    scope: frozenset[str]  # tool names this token may call
    run_id: str
    jti: str  # the only token identifier audit may store
    iat: int
    exp: int
    iss: str = ISSUER

    @property
    def aud(self) -> str:
        return f"mcp:{self.domain}"

    @property
    def act(self) -> dict[str, str]:
        return {"sub": f"agent:{self.agent}"}


@dataclass(frozen=True)
class GatewayClaims:
    sub: str
    agent: str
    run_id: str
    floor: Level
    jti: str
    iat: int
    exp: int


# ---- helpers ------------------------------------------------------------------------------

def _check_name(kind: str, value: Any) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise TokenError(f"invalid {kind} name")
    return value


def _check_ttl(ttl_s: Any, cap: int) -> int:
    if not isinstance(ttl_s, int) or isinstance(ttl_s, bool) or not 0 < ttl_s <= cap:
        raise TokenError(f"ttl must be between 1 and {cap} seconds")
    return ttl_s


def _active(principal: Principal) -> None:
    if not principal.active:
        raise TokenError("principal is not active")


def _decode(token: Any, key_name: str, *, audience: str | None, required: list[str]) -> dict:
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LEN:
        raise TokenError("malformed token")
    try:
        return jwt.decode(
            token,
            keys.load_key(key_name),
            algorithms=[ALGORITHM],  # pinned: rejects alg=none and algorithm confusion
            audience=audience,
            issuer=ISSUER if audience is not None else None,
            leeway=LEEWAY_S,
            options={"require": required, "verify_signature": True},
        )
    # Order matters: the expiry / audience / signature errors are subclasses of InvalidTokenError.
    except jwt.ExpiredSignatureError:
        raise TokenExpired("token expired") from None
    except jwt.InvalidAudienceError:
        raise WrongAudience("token audience does not match") from None
    except jwt.InvalidSignatureError:
        raise BadSignature("token signature is invalid") from None
    except jwt.InvalidIssuerError:
        raise TokenError("token issuer is invalid") from None
    except jwt.MissingRequiredClaimError:
        raise TokenError("token is missing a required claim") from None
    except jwt.ImmatureSignatureError:
        raise TokenError("token is not yet valid") from None
    except jwt.DecodeError:
        raise BadSignature("token could not be decoded") from None
    except jwt.InvalidTokenError:
        raise TokenError("token is invalid") from None


def _int_claim(payload: dict, name: str) -> int:
    v = payload.get(name)
    if not isinstance(v, int) or isinstance(v, bool):
        raise TokenError(f"token claim {name} is invalid")
    return v


def _str_claim(payload: dict, name: str) -> str:
    v = payload.get(name)
    if not isinstance(v, str) or not v:
        raise TokenError(f"token claim {name} is invalid")
    return v


def _act_agent(payload: dict) -> str:
    act = payload.get("act")
    sub = act.get("sub") if isinstance(act, dict) else None
    if not isinstance(sub, str) or not sub.startswith("agent:"):
        raise TokenError("token act claim is invalid")
    return _check_name("agent", sub[len("agent:"):])


# ---- sessions -----------------------------------------------------------------------------

_OPEN = "SELECT core.session_open(%s, %s, %s) AS ok"
_CHECK = "SELECT core.session_check(%s, %s) AS ok"
_REVOKE = "SELECT core.session_revoke(%s, %s) AS ok"
_REVOKE_USER = "SELECT core.session_revoke_user(%s) AS n"


def _sign_session(user_id: str, sid: str) -> str:
    now = int(time.time())
    claims = {"sub": user_id, "sid": sid, "iat": now, "exp": now + SESSION_TTL_S}
    return jwt.encode(claims, keys.load_key(keys.SESSION), algorithm=ALGORITHM)


def _session_claims(token: str) -> SessionClaims:
    payload = _decode(token, keys.SESSION, audience=None, required=["sub", "sid", "iat", "exp"])
    claims = SessionClaims(sub=_str_claim(payload, "sub"), sid=_str_claim(payload, "sid"),
                           iat=_int_claim(payload, "iat"), exp=_int_claim(payload, "exp"))
    if claims.exp - claims.iat > SESSION_TTL_S:
        raise TokenError("session lifetime too long")
    return claims


def issue_session(user_id: str) -> tuple[str, str]:
    """Open a session for an active human user. Returns (jwt, sid)."""
    sid = new_id("sid")
    rows = _db.fetch_sync(_OPEN, (sid, user_id, SESSION_TTL_S))
    if not rows or rows[0]["ok"] is not True:
        raise TokenError("cannot open a session for this user")
    return _sign_session(user_id, sid), sid


async def aissue_session(user_id: str) -> tuple[str, str]:
    sid = new_id("sid")
    rows = await _db.fetch_async(_OPEN, (sid, user_id, SESSION_TTL_S))
    if not rows or rows[0]["ok"] is not True:
        raise TokenError("cannot open a session for this user")
    return _sign_session(user_id, sid), sid


def verify_session(token: str) -> SessionClaims:
    """Signature, expiry (8 h, 5 s leeway) and that the sid is still open and the user active."""
    claims = _session_claims(token)
    rows = _db.fetch_sync(_CHECK, (claims.sid, claims.sub))
    if not rows or rows[0]["ok"] is not True:
        raise SessionRevoked("session revoked or expired; log in again")
    return claims


async def averify_session(token: str) -> SessionClaims:
    claims = _session_claims(token)
    rows = await _db.fetch_async(_CHECK, (claims.sid, claims.sub))
    if not rows or rows[0]["ok"] is not True:
        raise SessionRevoked("session revoked or expired; log in again")
    return claims


def revoke_session(sid: str, user_id: str) -> bool:
    """Logout: True if the open session `sid` of `user_id` was revoked. Take both from a verified
    session token (verify_session), so a caller can only end its own session."""
    rows = _db.fetch_sync(_REVOKE, (sid, user_id))
    return bool(rows and rows[0]["ok"])


async def arevoke_session(sid: str, user_id: str) -> bool:
    rows = await _db.fetch_async(_REVOKE, (sid, user_id))
    return bool(rows and rows[0]["ok"])


def revoke_user_sessions(admin: Principal, user_id: str, *, reason: str) -> int:
    """Offboarding / incident lever: end every open session of `user_id`. Returns how many.

    Platform admin only (checked here and by core.session_revoke_user in SQL); every attempt is
    audited as a `login` event with action revoke_sessions, a refused one with decision deny.
    """
    from corpai import audit
    from corpai.db import pg

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("revoking someone's sessions needs a reason")
    if not (admin.active and admin.role is Role.PLATFORM_ADMIN):
        audit.record("login", actor=admin.user_id, action="revoke_sessions", resource=f"user:{user_id}",
                     decision="deny", reason="not_platform_admin")
        raise PermissionError("only the platform admin may end another user's sessions")
    import psycopg

    try:
        with pg.principal_tx_sync(admin) as conn:
            row = conn.execute(_REVOKE_USER, (user_id,)).fetchone()
            # audited inside the transaction: if the audit write fails, nothing is revoked silently
            audit.record("login", actor=admin.user_id, action="revoke_sessions", resource=f"user:{user_id}",
                         decision="allow", reason=reason.strip()[:500], details={"revoked": int(row["n"])})
    except psycopg.errors.InsufficientPrivilege:
        audit.record("login", actor=admin.user_id, action="revoke_sessions", resource=f"user:{user_id}",
                     decision="deny", reason="refused_by_database")
        raise PermissionError("only the platform admin may end another user's sessions") from None
    return int(row["n"])


# ---- tool tokens --------------------------------------------------------------------------

def mint_tool_token(principal: Principal, *, domain: str, agent: str, run_id: str,
                    tools: Iterable[str], ttl_s: int = 60) -> str:
    _active(principal)
    _check_name("domain", domain)
    _check_name("agent", agent)
    scope = sorted({_check_name("tool", t) for t in tools})
    if not scope:
        raise TokenError("a tool token needs at least one tool")
    if not isinstance(run_id, str) or not run_id:
        raise TokenError("run_id is required")
    ttl_s = _check_ttl(ttl_s, MAX_TOOL_TTL_S)
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": f"mcp:{domain}",
        "sub": principal.user_id,
        "act": {"sub": f"agent:{agent}"},
        "scope": " ".join(scope),
        "run_id": run_id,
        "jti": new_id("jti"),
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(claims, keys.load_key(keys.TOOL), algorithm=ALGORITHM)


def verify_tool_token(token: str, *, domain: str) -> ToolClaims:
    """Signature, aud == mcp:<domain>, exp (5 s leeway), issuer, act and scope well-formed."""
    _check_name("domain", domain)
    payload = _decode(token, keys.TOOL, audience=f"mcp:{domain}",
                      required=["iss", "aud", "sub", "act", "scope", "run_id", "jti", "iat", "exp"])
    scope_text = _str_claim(payload, "scope")
    scope = frozenset(scope_text.split(" "))
    for t in scope:
        _check_name("tool", t)
    claims = ToolClaims(sub=_str_claim(payload, "sub"), agent=_act_agent(payload), domain=domain,
                        scope=scope, run_id=_str_claim(payload, "run_id"), jti=_str_claim(payload, "jti"),
                        iat=_int_claim(payload, "iat"), exp=_int_claim(payload, "exp"))
    if claims.exp - claims.iat > MAX_TOOL_TTL_S:
        raise TokenError("tool token lifetime too long")
    return claims


def require_scope(claims: ToolClaims, tool: str) -> None:
    if tool not in claims.scope:
        raise NotInScope("tool not in token scope")


# ---- gateway tokens -----------------------------------------------------------------------

def mint_gateway_token(principal: Principal, *, agent: str, run_id: str, floor: Level,
                       ttl_s: int = 900) -> str:
    _active(principal)
    _check_name("agent", agent)
    if not isinstance(run_id, str) or not run_id:
        raise TokenError("run_id is required")
    floor = Level(int(floor))
    ttl_s = _check_ttl(ttl_s, MAX_GATEWAY_TTL_S)
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": "gateway",
        "sub": principal.user_id,
        "act": {"sub": f"agent:{agent}"},
        "run_id": run_id,
        "floor": int(floor),
        "jti": new_id("jti"),
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(claims, keys.load_key(keys.GATEWAY), algorithm=ALGORITHM)


def verify_gateway_token(token: str) -> GatewayClaims:
    payload = _decode(token, keys.GATEWAY, audience="gateway",
                      required=["iss", "aud", "sub", "act", "run_id", "floor", "jti", "iat", "exp"])
    try:
        floor = Level(_int_claim(payload, "floor"))
    except ValueError:
        raise TokenError("token claim floor is invalid") from None
    claims = GatewayClaims(sub=_str_claim(payload, "sub"), agent=_act_agent(payload),
                           run_id=_str_claim(payload, "run_id"), floor=floor, jti=_str_claim(payload, "jti"),
                           iat=_int_claim(payload, "iat"), exp=_int_claim(payload, "exp"))
    if claims.exp - claims.iat > MAX_GATEWAY_TTL_S:
        raise TokenError("gateway token lifetime too long")
    return claims
