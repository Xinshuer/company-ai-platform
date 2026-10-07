"""Live model backend: any OpenAI-compatible endpoint through the openai SDK (DeepSeek today).

Key handling (ENGINEERING.md security rules 4 and 5): the API key is read from
os.environ[route.api_key_env] inside complete(), handed to a client that lives only for that call,
and never stored on this object, logged or put in an exception. Provider error bodies can echo
part of the key ("your api key ****abcd is invalid"), so errors are rebuilt from the HTTP status
and the exception class only, and raised outside the `except` block so no original exception is
chained to them.

One attempt per call: the SDK's own retries are off (max_retries=0) and the gateway does the
backoff, so retries are counted, audited and bounded in one place.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Protocol

import httpx2
import openai

from corpai.gateway.conf import RouteConfig
from corpai.gateway.egress import check_egress, check_host
from corpai.gateway.errors import BackendError, EgressDenied, MissingApiKey, RetryableBackendError
from corpai.gateway.models import BackendCall, BackendReply


class Backend(Protocol):
    async def complete(self, call: BackendCall, cfg: RouteConfig, *,
                       extra_body: Mapping[str, Any] | None = None) -> BackendReply: ...


def _usage(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    out: dict[str, int] = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = getattr(u, k, None) if u is not None else None
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            out[k] = v
    return out


def _tool_calls(msg: Any) -> list[dict]:
    calls = getattr(msg, "tool_calls", None) or []
    out = []
    for tc in calls:
        dump = tc.model_dump() if hasattr(tc, "model_dump") else dict(tc)
        out.append(dump)
    return out


class OpenAICompatBackend:
    """`transport` is for tests only (an httpx2.MockTransport standing in for the API)."""

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def __repr__(self) -> str:  # nothing secret lives here, but keep reprs boring on purpose
        return "OpenAICompatBackend()"

    async def complete(self, call: BackendCall, cfg: RouteConfig, *,
                       extra_body: Mapping[str, Any] | None = None) -> BackendReply:
        # Before anything else: the destination must be listed for this level and host.
        check_egress(cfg.egress, cfg.base_url, call.level)
        if not os.environ.get(cfg.api_key_env):
            raise MissingApiKey(f"{cfg.api_key_env} is not set; set it in the environment for live mode "
                                "or run with CORPAI_LLM=fake")

        async def _egress_hook(request: httpx2.Request) -> None:
            # Every request the SDK sends, including any redirect, must go to a listed host.
            check_host(cfg.egress, str(request.url))

        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "messages": call.messages,
            "max_tokens": call.max_tokens,
            "temperature": call.temperature,
        }
        if call.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if call.tools:
            kwargs["tools"] = call.tools
        if extra_body:
            kwargs["extra_body"] = dict(extra_body)

        failure: tuple[type[BackendError] | type[EgressDenied], str, int | None] | None = None
        resp = None
        try:
            # trust_env=False: proxy variables would add an outbound hop that egress.yaml does not list.
            async with httpx2.AsyncClient(transport=self._transport, timeout=cfg.timeout_s, trust_env=False,
                                          follow_redirects=False,
                                          event_hooks={"request": [_egress_hook]}) as http:
                client = openai.AsyncOpenAI(api_key=os.environ.get(cfg.api_key_env, ""), base_url=cfg.base_url,
                                            max_retries=0, timeout=cfg.timeout_s, http_client=http)
                resp = await client.chat.completions.create(**kwargs)
        except EgressDenied:
            failure = (EgressDenied, f"host is not listed for {cfg.egress} in config/egress.yaml", None)
        except openai.APIStatusError as e:
            status = int(getattr(e, "status_code", 0) or 0)
            cls = RetryableBackendError if status == 429 or status >= 500 else BackendError
            failure = (cls, f"{call.route} model backend returned HTTP {status}", status)
        except openai.APITimeoutError:
            failure = (RetryableBackendError, f"{call.route} model backend timed out", None)
        except openai.APIConnectionError as e:
            if isinstance(e.__cause__, EgressDenied):
                failure = (EgressDenied, f"host is not listed for {cfg.egress} in config/egress.yaml", None)
            else:
                failure = (RetryableBackendError, f"{call.route} model backend unreachable", None)
        except openai.OpenAIError as e:
            failure = (BackendError, f"{call.route} model backend failed: {type(e).__name__}", None)
        except httpx2.HTTPError as e:
            failure = (RetryableBackendError, f"{call.route} model backend failed: {type(e).__name__}", None)
        if failure is not None:
            cls, msg, status = failure
            if cls is EgressDenied:
                raise EgressDenied(msg)
            if cls is RetryableBackendError:
                raise RetryableBackendError(msg, status=status)
            raise BackendError(msg, status=status)

        if not getattr(resp, "choices", None):
            raise BackendError(f"{call.route} model backend returned no choices")
        msg = resp.choices[0].message
        return BackendReply(text=msg.content or "", tool_calls=_tool_calls(msg), usage=_usage(resp),
                            model=str(getattr(resp, "model", "") or cfg.model))
