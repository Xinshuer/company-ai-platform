"""OpenAI-compatible endpoint: POST /v1/chat/completions, authenticated with a gateway token.

This lets an unchanged OpenAI-SDK client (Clause Check's backend in M2) run against the gateway:
its "API key" is a gateway token minted by identity.tokens.mint_gateway_token. The token decides
who is asking (re-resolved from the directory on every request, so deactivation is immediate),
which agent, which run, and the sensitivity floor (-> ChatRequest.hwm). The `model` field of the
body is ignored: routing is the gateway's job, never the caller's.

Every token problem gets the same 401 body, so a caller learns nothing about why a token failed.
Stream B3 includes `router` in the app.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from corpai.gateway.errors import (
    BackendError,
    BudgetExceeded,
    ExternalUnavailable,
    GatewayBusy,
    GatewayError,
    LocalUnavailable,
    SecretDetected,
)
from corpai.gateway.models import ChatRequest
from corpai.ids import new_id
from corpai.identity import directory
from corpai.identity.tokens import TokenError, verify_gateway_token
from corpai.types import Principal

router = APIRouter()

MAX_BODY_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_TOKENS = 1200


def _error(status: int, message: str, type_: str, *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": type_}}, status_code=status, headers=headers)


def _unauthorized() -> JSONResponse:
    return _error(401, "invalid or missing gateway token", "invalid_request_error",
                  headers={"WWW-Authenticate": "Bearer"})


async def _authenticate(request: Request) -> tuple[Principal, Any] | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        claims = verify_gateway_token(token.strip())
    except TokenError:
        return None
    principal = await directory.get_principal(claims.sub)
    if principal is None or not principal.active or principal.user_id != claims.sub:
        return None
    return principal, claims


def _gateway(request: Request):
    # Tests (and B3, if it wants) may pin a gateway on app.state; otherwise the process singleton.
    gw = getattr(request.app.state, "gateway", None)
    if gw is None:
        from corpai.gateway import get_gateway
        gw = get_gateway()
    return gw


def _parse_body(body: Any) -> dict[str, Any] | str:
    """The ChatRequest fields from an OpenAI request body, or an error message."""
    if not isinstance(body, dict):
        return "the request body must be a JSON object"
    if body.get("stream"):
        return "streaming is not supported by the gateway; send stream=false"
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
        return "messages must be a non-empty list of message objects"
    max_tokens = body.get("max_tokens", body.get("max_completion_tokens")) or DEFAULT_MAX_TOKENS
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        return "max_tokens must be a positive integer"
    temperature = body.get("temperature", 0.2)
    if temperature is None:
        temperature = 0.2
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or not 0 <= temperature <= 2:
        return "temperature must be a number between 0 and 2"
    tools = body.get("tools")
    if tools is not None and (not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools)):
        return "tools must be a list of tool objects"
    rf = body.get("response_format")
    json_mode = isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema")
    return {"messages": messages, "max_tokens": max_tokens, "temperature": float(temperature),
            "tools": tools or None, "json_mode": json_mode}


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    auth = await _authenticate(request)
    if auth is None:
        return _unauthorized()
    principal, claims = auth

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _error(413, "request body too large", "invalid_request_error")
    try:
        body = json.loads(raw)
    except ValueError:
        return _error(400, "the request body must be valid JSON", "invalid_request_error")
    parsed = _parse_body(body)
    if isinstance(parsed, str):
        return _error(400, parsed, "invalid_request_error")

    req = ChatRequest(principal=principal, hwm=claims.floor, purpose=f"compat.{claims.agent}",
                      run_id=claims.run_id, agent=claims.agent, **parsed)
    try:
        result = await _gateway(request).chat(req)
    except LocalUnavailable as e:
        return _error(503, str(e), "local_unavailable", headers={"Retry-After": "60"})
    except ExternalUnavailable as e:
        return _error(503, str(e), "external_unavailable", headers={"Retry-After": "5"})
    except GatewayBusy as e:
        return _error(503, str(e), "gateway_busy", headers={"Retry-After": "5"})
    except SecretDetected as e:
        return _error(400, str(e), "secret_detected")
    except BudgetExceeded as e:
        return _error(429, str(e), "budget_exceeded")
    except BackendError as e:
        return _error(502, str(e), "backend_error")
    except GatewayError as e:
        return _error(502, str(e), "gateway_error")
    except PermissionError:
        return _unauthorized()
    except (ValueError, TypeError):
        return _error(400, "the request could not be processed; check the message format", "invalid_request_error")

    message: dict[str, Any] = {"role": "assistant", "content": result.text,
                               "tool_calls": result.tool_calls or None}
    return JSONResponse({
        "id": f"chatcmpl-{new_id('cc')}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": result.model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if result.tool_calls else "stop"}],
        "usage": {k: int(result.usage.get(k, 0)) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
        # Not part of the OpenAI schema; SDKs ignore unknown keys. Lets callers show the route badge.
        "corpai": {"route": str(result.route), "level": int(result.level), "audit_seq": result.audit_seq},
    })
