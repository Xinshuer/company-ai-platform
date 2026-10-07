"""ModelGateway.chat: the single exit for model traffic (architecture §8, design-review 二.2 / 二.9).

The nine rules run in the spec's order:

 1. scrub_secrets on every message (both routes); a secret classify_text still sees -> refused;
 2. level = max(hwm, classify_text(message text)); classifier error -> L4; no person-name
    gazetteer loaded -> at least L3 (a name the classifier cannot recognise must not go external);
 3. route = external only if level <= L2, else local;
 4. local + local_down -> LocalUnavailable (never external); external + external_down -> local,
    reason external_down_fallback;
 5. external only: dlp_scan of the whole outbound payload; blocked -> local, reason dlp_upgrade
    (rule 4 is checked again for the new route);
 6. per-route concurrency per event loop, wait queue_timeout_s, then GatewayBusy; the outage
    switches are read again once a slot is free and before every retry (a switch flipped while a
    call queued still stops it);
 7. per-department per-day (UTC) token budget: reserve before, settle that reservation after,
    also when the call fails or is cancelled; else BudgetExceeded;
 8. backend call with a per-attempt timeout and exponential backoff on 429/5xx/timeouts;
 9. audit (never message text), trace span (masked by level), external payload in ops.egress_log.

Two deliberate details: the egress row is written *before* the payload is sent, in the same
transaction as the budget reservation, so nothing can leave unlogged; and the route is never a
parameter — callers only influence it by raising hwm.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from typing import Any

from corpai import audit
from corpai.classify import classify_text, dlp_scan, gazetteer_size, scrub_secrets
from corpai.gateway import ops
from corpai.gateway.backend import Backend
from corpai.gateway.conf import GatewayConfig, RouteConfig, load_config
from corpai.gateway.egress import check_egress
from corpai.gateway.errors import (
    BackendError,
    BudgetExceeded,
    EgressDenied,
    ExternalUnavailable,
    GatewayBusy,
    GatewayError,
    LocalUnavailable,
    RetryableBackendError,
    SecretDetected,
)
from corpai.gateway.models import BackendCall, BackendReply, ChatRequest, ChatResult
from corpai.types import Level, Route

log = logging.getLogger(__name__)

# ---- message helpers ------------------------------------------------------------------------

def _scrub(obj: Any) -> tuple[Any, int]:
    """Deep copy of `obj` with scrub_secrets applied to every string, dict keys included (whatever
    the backend receives is scrubbed, not only the fields we expect content in)."""
    if isinstance(obj, str):
        return scrub_secrets(obj)
    if isinstance(obj, dict):
        out, n = {}, 0
        for k, v in obj.items():
            key, m1 = _scrub(k) if isinstance(k, str) else (k, 0)
            out[key], m2 = _scrub(v)
            n += m1 + m2
        return out, n
    if isinstance(obj, (list, tuple)):
        items, n = [], 0
        for v in obj:
            s, m = _scrub(v)
            items.append(s)
            n += m
        return items, n
    return obj, 0


def _texts(obj: Any) -> list[str]:
    """Every string, number and dict key inside messages / tools, in order. Nothing is skipped as
    "plumbing": anything the backend receives can carry data, so everything is classified."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, bool) or obj is None:
        return []
    if isinstance(obj, (int, float)):
        return [str(obj)]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in (*_texts(k), *_texts(v))]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _texts(v)]
    return [str(obj)]


def message_text(messages: list[dict]) -> str:
    return "\n".join(_texts(messages))


def _trace_text(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        body = "\n".join(_texts([v for k, v in m.items() if k != "role"]))
        lines.append(f"{m.get('role', '?')}: {body}")
    return "\n".join(lines)


def _as_level(v: Any) -> Level:
    """hwm from the caller; anything that is not a valid level counts as L4 (fail closed)."""
    try:
        if isinstance(v, bool):
            raise ValueError
        return Level(int(v))
    except (TypeError, ValueError):
        return Level.L4


def _approx_tokens(text: str) -> int:
    # ~4 characters per token is a deliberate over-estimate for the mostly-English corpus, used
    # only to reserve budget before the call; the reservation is settled with real usage after.
    return len(text) // 4 + 1


def _validate(req: ChatRequest) -> None:
    if not req.principal.active:
        raise PermissionError("the principal is not active")
    if not isinstance(req.messages, list) or not req.messages or not all(isinstance(m, dict) for m in req.messages):
        raise ValueError("messages must be a non-empty list of OpenAI-format dicts")
    for name in ("purpose", "run_id", "agent"):
        v = getattr(req, name)
        if not isinstance(v, str) or not v or len(v) > 200:
            raise ValueError(f"ChatRequest.{name} must be a non-empty string")
    if req.tools is not None and not isinstance(req.tools, list):
        raise ValueError("tools must be a list or None")


def _switch_reason(e: GatewayError) -> str | None:
    if isinstance(e, LocalUnavailable):
        return "local_down"
    if isinstance(e, ExternalUnavailable):
        return "external_down"
    return None


# ---- the gateway ----------------------------------------------------------------------------

class ModelGateway:
    def __init__(self, backend: Backend, *, config: GatewayConfig | None = None, tracer=None) -> None:
        self.backend = backend
        self.config = config or load_config()
        self._tracer = tracer
        # asyncio primitives belong to one event loop; tests and the server may run several.
        self._sems: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[Route, asyncio.Semaphore]] = \
            weakref.WeakKeyDictionary()

    @property
    def tracer(self):
        if self._tracer is None:
            from corpai.trace import tracer
            return tracer
        return self._tracer

    def _sem(self, route: Route) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        per_loop = self._sems.get(loop)
        if per_loop is None:
            per_loop = self._sems[loop] = {
                r: asyncio.Semaphore(self.config.route(r).max_concurrency) for r in Route}
        return per_loop[route]

    async def _audit(self, event: str, req: ChatRequest, *, route: Route, level: Level, decision: str,
                     reason: str, model: str, details: dict[str, Any]) -> int:
        return await audit.arecord(
            event, actor=req.principal.user_id, act_chain=[f"agent:{req.agent}"], action="chat",
            resource=f"model:{model}", decision=decision, reason=reason, route=str(route), level=int(level),
            run_id=req.run_id, details={"purpose": req.purpose, "agent": req.agent, **details})

    async def _switched_off(self, route: Route) -> GatewayError | None:
        """The error to raise if `route` is switched off right now (read fresh from the database)."""
        switches = await ops.get_switches()
        if route is Route.LOCAL and switches["local_down"]:
            return LocalUnavailable()
        if route is Route.EXTERNAL and switches["external_down"]:
            return ExternalUnavailable()
        return None

    async def _call_with_retry(self, call: BackendCall, cfg: RouteConfig) -> tuple[BackendReply, int]:
        retry = self.config.retry
        attempt = 0
        while True:
            attempt += 1
            if attempt > 1:
                # A retry can come seconds after the first attempt: honour a switch flipped meanwhile.
                off = await self._switched_off(call.route)
                if off is not None:
                    raise off
            retryable: RetryableBackendError | None = None
            fatal: BackendError | None = None
            try:
                reply = await asyncio.wait_for(
                    self.backend.complete(call, cfg, extra_body=self.config.extra_body), cfg.timeout_s)
                return reply, attempt
            except RetryableBackendError as e:
                retryable = e
            except TimeoutError:
                retryable = RetryableBackendError(f"{call.route} model backend timed out")
            except GatewayError:
                raise  # MissingApiKey, EgressDenied, non-retryable BackendError: our own fixed messages
            except Exception as e:  # noqa: BLE001
                # A backend bug: keep the class name only, its message may quote the payload.
                fatal = BackendError(f"{call.route} model backend failed: {type(e).__name__}")
            # Raised outside the except blocks so no original exception is chained to ours.
            if fatal is not None:
                raise fatal
            assert retryable is not None
            if attempt >= retry.max_attempts:
                raise BackendError(f"{call.route} model backend failed after {attempt} attempts",
                                   status=retryable.status, retryable=True)
            await asyncio.sleep(retry.delay(attempt))

    async def _release_cancelled(self, req: ChatRequest, *, reservation_id: int | None, route: Route,
                                 level: Level, model: str, details: dict[str, Any]) -> None:
        """A call cancelled mid-flight (client gone, a timeout around chat()): refund the reservation
        and leave a model_call record next to any egress row already logged, so audit and egress
        stay reconcilable. Runs shielded: the cancellation must not cancel the clean-up too."""
        async def _cleanup() -> None:
            if reservation_id is not None:
                await ops.settle(req.principal, run_id=req.run_id, reservation_id=reservation_id, actual=0)
            await self._audit("model_call", req, route=route, level=level, decision="error", reason="cancelled",
                              model=model, details=details)

        task = asyncio.ensure_future(_cleanup())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass  # cancelled again while waiting: the clean-up task still runs to completion
        except Exception as e:  # noqa: BLE001 - never replace the cancellation with a clean-up error
            log.warning("gateway clean-up after cancellation failed: %s", type(e).__name__)

    async def chat(self, req: ChatRequest) -> ChatResult:
        _validate(req)
        p = req.principal

        # 1. secrets never reach a model, whichever route
        messages, redactions = _scrub(req.messages)
        tools, tool_redactions = _scrub(req.tools) if req.tools is not None else (None, 0)
        redactions += tool_redactions

        # 2. level from the caller's hwm and from the content itself
        hwm = _as_level(req.hwm)
        public = req.public_only is True and hwm == Level.L1
        content = classify_text(message_text(messages), public=public)
        level = max(hwm, content.level)
        findings = sorted(content.kinds)
        reason = "route_by_level"
        if gazetteer_size() == 0 and level < Level.L3:
            # Person names are recognised through the gazetteer (directory, CRM contacts,
            # candidates). Without it "Jonas Becker" classifies as L1; unsure means higher (rule 4).
            level = Level.L3
            findings = sorted({*findings, "gazetteer_unloaded"})
            reason = "gazetteer_unloaded"

        # 3. route from the level only
        route = Route.EXTERNAL if level <= Level.L2 else Route.LOCAL
        switches = await ops.get_switches()

        # 4. external down: the safe direction is allowed
        if route is Route.EXTERNAL and switches["external_down"]:
            route, reason = Route.LOCAL, "external_down_fallback"

        # 5. DLP on everything that would leave (messages and tool definitions)
        if route is Route.EXTERNAL:
            dlp = dlp_scan("\n".join(_texts([messages, tools])), route=Route.EXTERNAL, public=public)
            if dlp.blocked:
                level = max(level, dlp.level)
                findings = sorted(set(findings) | {f.kind for f in dlp.findings})
                route, reason = Route.LOCAL, "dlp_upgrade"

        cfg = self.config.route(route)
        base_details: dict[str, Any] = {"hwm": int(hwm), "content_level": int(content.level),
                                        "findings": findings, "redactions": redactions,
                                        "public_only": public}

        # 1 (again): classify_text and scrub_secrets share their detectors, so a secret seen here
        # means scrubbing missed it. Refuse instead of sending it to either route.
        if "secret" in content.kinds:
            await self._audit("model_call", req, route=route, level=level, decision="deny",
                              reason="secret_unscrubbed", model=cfg.model, details=dict(base_details))
            raise SecretDetected()

        if reason != "route_by_level":
            await self._audit("route_decision", req, route=route, level=level, decision="reroute",
                              reason=reason, model=cfg.model, details=dict(base_details))

        # 4 (again, for the final route): never fall back from local to external
        if route is Route.LOCAL and switches["local_down"]:
            await self._audit("model_call", req, route=route, level=level, decision="deny", reason="local_down",
                              model=cfg.model, details=dict(base_details))
            raise LocalUnavailable()

        # The egress inventory must allow this destination at this level (fake mode included).
        try:
            check_egress(cfg.egress, cfg.base_url, level)
        except EgressDenied:
            await self._audit("model_call", req, route=route, level=level, decision="deny", reason="egress_denied",
                              model=cfg.model, details=dict(base_details))
            raise

        max_tokens = max(1, min(int(req.max_tokens), self.config.max_tokens_cap))
        call = BackendCall(route=route, level=level, purpose=req.purpose, messages=messages,
                           json_mode=bool(req.json_mode), max_tokens=max_tokens,
                           temperature=float(req.temperature), tools=tools)

        async with self.tracer.span(f"llm:{req.purpose}", kind="llm", run_id=req.run_id, principal=p,
                                    level=level, input=_trace_text(messages)) as sp:
            sp.set_attr("route", str(route))
            sp.set_attr("reason", reason)
            sp.set_attr("model", cfg.model)
            sp.set_attr("agent", req.agent)
            sp.set_attr("purpose", req.purpose)

            # 6. concurrency
            sem = self._sem(route)
            try:
                await asyncio.wait_for(sem.acquire(), cfg.queue_timeout_s)
            except TimeoutError:
                await self._audit("model_call", req, route=route, level=level, decision="deny",
                                  reason="gateway_busy", model=cfg.model, details=dict(base_details))
                raise GatewayBusy(f"the {route} model is busy; try again in a moment") from None

            started = time.monotonic()
            reservation_id: int | None = None
            egress_id: int | None = None
            try:
                # 4 (again, after the wait): an admin may have switched this route off meanwhile.
                off = await self._switched_off(route)
                if off is not None:
                    await self._audit("model_call", req, route=route, level=level, decision="deny",
                                      reason=_switch_reason(off) or "switched_off", model=cfg.model,
                                      details=dict(base_details))
                    raise off

                # 7. budget (+ the egress row for external calls, in the same transaction)
                reserve = _approx_tokens(message_text(messages)) + max_tokens
                egress = None
                if route is Route.EXTERNAL:
                    payload = json.dumps({"model": cfg.model, "messages": messages, "tools": tools,
                                          "json_mode": call.json_mode, "max_tokens": max_tokens,
                                          "temperature": call.temperature},
                                         ensure_ascii=False, sort_keys=True)
                    egress = {"destination": cfg.egress, "route": str(route), "level": int(level),
                              "payload": payload}
                left, reservation_id, egress_id = await ops.reserve_and_log(
                    p, run_id=req.run_id, tokens=reserve, egress=egress)
                if left < 0:
                    await self._audit("budget_exceeded", req, route=route, level=level, decision="deny",
                                      reason="budget_exceeded", model=cfg.model,
                                      details={**base_details, "dept": str(p.dept), "reserved_tokens": reserve})
                    raise BudgetExceeded("今日模型额度已用完 / the department's model budget for today is used up; "
                                         "ask your department head")

                # 8. the call
                try:
                    reply, attempts = await self._call_with_retry(call, cfg)
                except GatewayError as e:
                    rid, reservation_id = reservation_id, None
                    await ops.settle(p, run_id=req.run_id, reservation_id=rid, actual=0)
                    latency = int((time.monotonic() - started) * 1000)
                    switched = _switch_reason(e)
                    await self._audit("model_call", req, route=route, level=level,
                                      decision="deny" if switched else "error",
                                      reason=switched or "backend_error", model=cfg.model,
                                      details={**base_details, "error": type(e).__name__,
                                               "status": getattr(e, "status", None), "latency_ms": latency,
                                               "egress_id": egress_id})
                    raise

                latency = int((time.monotonic() - started) * 1000)
                usage = {k: int(reply.usage[k]) for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                         if k in reply.usage}
                actual = usage.get("total_tokens", reserve)
                rid, reservation_id = reservation_id, None
                await ops.settle(p, run_id=req.run_id, reservation_id=rid, actual=actual)
            except BaseException as e:
                if isinstance(e, Exception):
                    raise  # every Exception path above settles (or never reserved) and audits itself
                # Cancellation (client disconnect, an anyio cancel scope, a timeout around chat()):
                # CancelledError is a BaseException, so it skipped the handlers above.
                await self._release_cancelled(
                    req, reservation_id=reservation_id, route=route, level=level, model=cfg.model,
                    details={**base_details, "error": type(e).__name__, "egress_id": egress_id,
                             "latency_ms": int((time.monotonic() - started) * 1000)})
                raise
            finally:
                sem.release()

            # 9. audit (metadata only) and trace
            seq = await self._audit("model_call", req, route=route, level=level, decision="allow", reason=reason,
                                    model=reply.model or cfg.model,
                                    details={**base_details, **usage, "latency_ms": latency, "attempts": attempts,
                                             "egress_id": egress_id, "json_mode": call.json_mode,
                                             "tool_calls": len(reply.tool_calls)})
            sp.set_output(reply.text)
            sp.set_attr("audit_seq", seq)
            sp.set_attr("usage", usage)
            sp.set_attr("latency_ms", latency)

        return ChatResult(text=reply.text, tool_calls=reply.tool_calls, route=route,
                          model=reply.model or cfg.model, level=level, usage=usage, latency_ms=latency,
                          audit_seq=seq)
