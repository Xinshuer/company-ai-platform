"""Authentication of every MCP tool call (architecture §11, design-review 二.4).

The server trusts nothing the caller says about who it is: the principal comes from a short-lived
tool token (aud = mcp:<domain>, sub = the human, act = the agent, scope = the tools) and is then
re-read from the directory, so a deactivated user loses access on the next call. Effective rights
= user rules ∩ the agent's allowlist (policy.tool_allowed) ∩ the token scope.

Token text never leaves this module: it is not logged, not put in exceptions, not audited (only
the jti of a verified token is). Every refusal raises ToolDenied with a fixed message and is
audited as tool_call / deny.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from corpai import audit
from corpai.identity import directory, tokens
from corpai.policy import tool_allowed
from corpai.types import Principal

log = logging.getLogger(__name__)


class ToolDenied(ToolError):
    """The caller may not run this tool. The message is fixed text plus a policy reason; it never
    contains token material. MCP returns it to the client as an error result (isError)."""

    PREFIX = "denied: "

    def __init__(self, reason: str):
        super().__init__(self.PREFIX + reason)
        self.reason = reason


@dataclass(frozen=True)
class AuthContext:
    principal: Principal
    agent: str
    run_id: str
    jti: str
    domain: str
    tool: str
    rule_id: str

    @property
    def act_chain(self) -> list[str]:
        return [f"agent:{self.agent}"]


def _bearer(ctx: Any) -> str | None:
    rc = getattr(ctx, "request_context", None)
    request = getattr(rc, "request", None) if rc is not None else None
    headers = getattr(request, "headers", None)
    if headers is None:
        return None  # in-process call without HTTP: there is no token, so no identity
    value = headers.get("authorization")
    if not isinstance(value, str):
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


_TOKEN_REASONS: dict[type, str] = {
    tokens.TokenExpired: "token_expired",
    tokens.WrongAudience: "token_wrong_audience",
    tokens.BadSignature: "token_bad_signature",
}


async def _deny(domain: str, tool: str, reason: str, message: str, *, actor: str = "anonymous",
                agent: str | None = None, run_id: str | None = None, jti: str | None = None,
                rule_id: str | None = None) -> ToolDenied:
    details: dict[str, Any] = {"domain": domain, "tool": tool}
    if jti:
        details["jti"] = jti
    if rule_id:
        details["rule_id"] = rule_id
    try:
        await audit.arecord("tool_call", actor=actor, act_chain=[f"agent:{agent}"] if agent else [],
                            action=f"{domain}.{tool}", decision="deny", reason=reason, run_id=run_id,
                            details=details)
    except Exception as e:  # the refusal stands even if the audit write fails
        log.error("audit of a tool denial failed: %s", type(e).__name__)
    return ToolDenied(message)


async def authenticate(ctx: Any, domain: str, tool: str) -> AuthContext:
    """Bearer token -> verify_tool_token(domain) -> require_scope -> re-resolve the principal ->
    policy.tool_allowed. Returns the AuthContext or raises ToolDenied (audited)."""
    token = _bearer(ctx)
    if token is None:
        raise await _deny(domain, tool, "token_missing", "a valid tool token is required")
    claims: tokens.ToolClaims | None = None
    reason = "token_invalid"
    try:
        claims = tokens.verify_tool_token(token, domain=domain)
    except tokens.TokenError as e:
        reason = next((r for cls, r in _TOKEN_REASONS.items() if isinstance(e, cls)), "token_invalid")
    del token  # nothing below needs it
    if claims is None:
        # Raised outside the except block on purpose: no __context__ chain back to frames that held the token.
        raise await _deny(domain, tool, reason, "invalid or expired tool token")
    try:
        tokens.require_scope(claims, tool)
    except tokens.NotInScope:
        raise await _deny(domain, tool, "not_in_scope", "the tool token does not cover this tool",
                          actor=claims.sub, agent=claims.agent, run_id=claims.run_id, jti=claims.jti) from None
    principal = await directory.get_principal(claims.sub)
    if principal is None or not principal.active:
        raise await _deny(domain, tool, "user_inactive_or_unknown", "the user is inactive or unknown",
                          actor=claims.sub, agent=claims.agent, run_id=claims.run_id, jti=claims.jti)
    dec = tool_allowed(principal, claims.agent, domain, tool)
    if not dec.allow:
        raise await _deny(domain, tool, "policy_deny", dec.reason, actor=principal.user_id, agent=claims.agent,
                          run_id=claims.run_id, jti=claims.jti, rule_id=dec.rule_id)
    return AuthContext(principal=principal, agent=claims.agent, run_id=claims.run_id, jti=claims.jti,
                       domain=domain, tool=tool, rule_id=dec.rule_id)
