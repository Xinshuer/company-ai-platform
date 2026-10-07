"""The tool client agents use to call the MCP servers (architecture §11, design-review 二.4).

Every call mints a fresh tool token for exactly one tool (scope=[tool], 60 s), opens an MCP client
over streamable HTTP with that token as the Bearer header, calls the tool and parses the envelope.
The token lives only inside call(): it never reaches the caller, graph state, checkpoints,
traces, logs or exception messages (every failure is re-raised as McpCallError with a fixed
message, raised outside the except block so no exception chain points back at it).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from corpai.config import settings
from corpai.types import Level, Principal, Trust

log = logging.getLogger(__name__)

TOKEN_TTL_S = 60
_TEST_BASE = "http://127.0.0.1:8000"  # host the MCP servers' DNS-rebinding check accepts
DENIED_PREFIX = "denied: "
# MCP wraps a tool's ToolError as "Error executing tool <name>: <message>"; ToolDenied messages start "denied: ".
_DENIED = re.compile(r"^(?:Error executing tool [a-z0-9_]+: )?denied: (.{1,500})", re.S)


class McpCallError(RuntimeError):
    """Transport or protocol failure. Fixed message: never token text or server bodies."""


@dataclass(frozen=True)
class ToolEnvelope:
    """The §11 result envelope. Write tools also fill proposal_id / status / tier."""

    ok: bool
    data: Any = None
    label: Mapping[str, Any] = field(default_factory=dict)
    denied: list[dict] = field(default_factory=list)
    masked_fields: list[str] = field(default_factory=list)
    error: str | None = None
    message: str | None = None
    proposal_id: str | None = None
    status: str | None = None
    tier: int | None = None

    @property
    def level(self) -> Level:
        """The data's level; a missing or malformed label counts as L4 (fail closed)."""
        try:
            return Level(int(self.label["level"]))
        except (KeyError, TypeError, ValueError):
            return Level.L4

    @property
    def trust(self) -> Trust:
        try:
            return Trust(self.label["trust"])
        except (KeyError, TypeError, ValueError):
            return Trust.UNTRUSTED

    @property
    def sources(self) -> list[str]:
        return list(self.label.get("sources") or ())

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ToolEnvelope":
        if not isinstance(d, Mapping) or "ok" not in d:
            return cls.failure("bad_envelope", "the tool returned an unexpected result")
        tier = d.get("tier")
        return cls(ok=bool(d.get("ok")), data=d.get("data"), label=dict(d.get("label") or {}),
                   denied=list(d.get("denied") or []), masked_fields=list(d.get("masked_fields") or []),
                   error=d.get("error"), message=d.get("message"), proposal_id=d.get("proposal_id"),
                   status=d.get("status"), tier=int(tier) if isinstance(tier, int) else None)

    @classmethod
    def failure(cls, error: str, message: str, *, denied: list[dict] | None = None) -> "ToolEnvelope":
        # L1/untrusted is meaningless for a result with no data; L4 keeps callers fail-closed if they
        # look at the label anyway.
        return cls(ok=False, error=error, message=message, denied=denied or [],
                   label={"level": int(Level.L4), "dept": None, "trust": str(Trust.UNTRUSTED), "sources": []})

    def to_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "label": dict(self.label), "denied": self.denied,
                "masked_fields": self.masked_fields, "error": self.error, "message": self.message,
                "proposal_id": self.proposal_id, "status": self.status, "tier": self.tier}


class McpClient:
    def __init__(self, base_url: str | None = None, *, asgi_app: Any = None, timeout_s: float = 60.0):
        self._asgi_app = asgi_app
        self._base = (base_url or (_TEST_BASE if asgi_app is not None else settings().api_base)).rstrip("/")
        self._timeout_s = timeout_s

    def endpoint(self, domain: str) -> str:
        return f"{self._base}/mcp/{domain}/mcp"

    async def call(self, *, principal: Principal, agent: str, run_id: str, domain: str, tool: str,
                   args: Mapping[str, Any] | None = None) -> ToolEnvelope:
        """Call one tool as `principal` through `agent`. Denials come back as ok=false envelopes
        with error "tool_denied" and a `denied` entry; transport failures raise McpCallError."""
        from corpai.identity import tokens

        reason = ""
        try:
            token = tokens.mint_tool_token(principal, domain=domain, agent=agent, run_id=run_id, tools=[tool],
                                           ttl_s=TOKEN_TTL_S)
        except tokens.TokenError as e:
            reason = str(e)  # fixed text from identity.tokens, e.g. "principal is not active"
            token = None
        if token is None:
            return ToolEnvelope.failure("tool_denied", f"cannot call {domain}.{tool}: {reason}",
                                        denied=[{"id": f"{domain}.{tool}", "reason": reason}])
        failure: str | None = None
        result = None
        try:
            result = await self._call(token, domain, tool, dict(args or {}))
        except Exception as e:  # noqa: BLE001 - re-raised below with a fixed message
            failure = type(e).__name__
        del token
        if failure is not None:
            log.warning("MCP call %s.%s failed: %s", domain, tool, failure)
            raise McpCallError(f"calling {domain}.{tool} failed ({failure}); try again later")
        return self._parse(domain, tool, result)

    async def _call(self, token: str, domain: str, tool: str, args: dict):
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"}
        kwargs: dict[str, Any] = {"headers": headers, "timeout": self._timeout_s, "follow_redirects": False}
        if self._asgi_app is not None:
            kwargs["transport"] = httpx2.ASGITransport(app=self._asgi_app)
            kwargs["base_url"] = self._base
        else:
            kwargs["trust_env"] = False  # no proxy from the environment sees the token
        async with httpx2.AsyncClient(**kwargs) as http:
            async with Client(streamable_http_client(self.endpoint(domain), http_client=http),
                              cache=None) as client:
                return await client.call_tool(tool, args)

    @staticmethod
    def _parse(domain: str, tool: str, result: Any) -> ToolEnvelope:
        text = ""
        for part in getattr(result, "content", None) or []:
            if getattr(part, "type", None) == "text":
                text += part.text
        if getattr(result, "is_error", False):
            m = _DENIED.match(text)
            if m:
                reason = m.group(1)
                return ToolEnvelope.failure("tool_denied", f"no permission for {domain}.{tool}: {reason}",
                                            denied=[{"id": f"{domain}.{tool}", "reason": reason}])
            # Other errors (argument validation, server failure) may echo argument values: not passed on.
            return ToolEnvelope.failure("tool_error", f"{domain}.{tool} failed; check the arguments")
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, Mapping) and "ok" in structured:
            return ToolEnvelope.from_dict(structured)
        try:
            return ToolEnvelope.from_dict(json.loads(text))
        except ValueError:
            return ToolEnvelope.failure("bad_envelope", f"{domain}.{tool} returned an unexpected result")
