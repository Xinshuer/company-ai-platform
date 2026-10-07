"""FakeLLM: the deterministic backend used when CORPAI_LLM=fake (tests, CI, demo without a key).

Feature modules register responders by purpose prefix:

    fake_llm().register("qa.", fn)     # fn(messages, *, route, json_mode) -> str | dict

The longest matching prefix wins. A responder may be sync or async, may return a dict
{"content": str, "tool_calls": [...]} to fake tool calls, and may raise RetryableBackendError to
exercise the gateway's retry path. Every call is recorded in `.calls` (route, level, purpose,
messages, json_mode) — after secret scrubbing, i.e. exactly what a real backend would have seen.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any, Awaitable, Callable, Mapping, Union

from corpai.gateway.conf import RouteConfig
from corpai.gateway.models import BackendCall, BackendReply
from corpai.types import Route

Responder = Callable[..., Union[str, dict, Awaitable[Union[str, dict]]]]

_DOC_RE = re.compile(r"\[doc:[^\]\s]{1,200}\]")


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings(v)]
    return []


def _approx_tokens(text: str) -> int:
    return len(text) // 4 + 1


def default_responder(messages: list[dict], *, route: Route | str, json_mode: bool) -> str:
    """"[fake/<route>] " plus the [doc:…] markers found in the context, so citation checks work."""
    text = "\n".join(_strings([m.get("content") for m in messages if isinstance(m, dict)]))
    docs = list(dict.fromkeys(_DOC_RE.findall(text)))
    if docs:
        body = "Answer based on " + " ".join(docs[:8])
    else:
        body = "No sources in context."
    answer = f"[fake/{route}] {body}"
    if json_mode:
        return json.dumps({"answer": answer, "citations": docs[:8]}, ensure_ascii=False)
    return answer


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._responders: dict[str, Responder] = {}

    def register(self, prefix: str, fn: Responder) -> None:
        if not isinstance(prefix, str):
            raise TypeError("prefix must be a string")
        self._responders[prefix] = fn

    def unregister(self, prefix: str) -> None:
        self._responders.pop(prefix, None)

    def responder_for(self, purpose: str) -> Responder:
        best = max((p for p in self._responders if purpose.startswith(p)), key=len, default=None)
        return self._responders[best] if best is not None else default_responder

    async def complete(self, call: BackendCall, cfg: RouteConfig, *,
                       extra_body: Mapping[str, Any] | None = None) -> BackendReply:
        self.calls.append({
            "route": str(call.route), "level": int(call.level), "purpose": call.purpose,
            "messages": call.messages, "json_mode": call.json_mode, "tools": call.tools,
            "max_tokens": call.max_tokens, "temperature": call.temperature,
        })
        fn = self.responder_for(call.purpose)
        out = fn(call.messages, route=str(call.route), json_mode=call.json_mode)
        if inspect.isawaitable(out):
            out = await out
        tool_calls: list[dict] = []
        if isinstance(out, dict):
            text = str(out.get("content") or "")
            tool_calls = list(out.get("tool_calls") or [])
        elif isinstance(out, str):
            text = out
        else:
            raise TypeError("fake responders must return str or dict")
        prompt = _approx_tokens("\n".join(_strings(call.messages)))
        completion = _approx_tokens(text)
        return BackendReply(text=text, tool_calls=tool_calls, model=cfg.model,
                            usage={"prompt_tokens": prompt, "completion_tokens": completion,
                                   "total_tokens": prompt + completion})


_singleton: FakeLLM | None = None


def fake_llm() -> FakeLLM:
    """The process singleton used by get_gateway() in fake mode."""
    global _singleton
    if _singleton is None:
        _singleton = FakeLLM()
    return _singleton
