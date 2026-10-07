"""LangGraph runtime shared by every graph (architecture §12, design-review 二.3).

Graph nodes never talk to the database, the MCP servers or the model directly. They use the three
helpers here, which keep the session's sensitivity high-water mark (hwm) honest:

    retrieve(rt, state, query, *, purpose, k=8)      -> (RetrievalResult, state update)
    call_tool(rt, state, domain, tool, args)         -> (ToolEnvelope, state update)
    llm(rt, state, messages, *, purpose, **kw)       -> ChatResult   (llm_update() gives its update)
    approval_gate(rt, proposal_id)                   -> resume value (LangGraph interrupt)

Who is asking, which run, which agent: RunContext, passed as `context=` (context_schema), never
part of the state. The model can therefore not write it, and it never reaches a checkpoint.

The hwm only goes up: BaseState.sensitivity_hwm has the reducer max_level, every helper returns an
update that raises it to the level of what entered the session (retrieved chunks, tool data, the
gateway's own classification), and llm() passes it to the gateway, which picks the route from it.
A new thread is the only reset.

`Step` bundles the helpers for one node so consecutive calls see each other's hwm and the node
returns one update. `@node(caps={...})` declares a node's Rule-of-Two capabilities (checked by
tests/security/test_rule_of_two.py) and wraps it in a trace span.
"""

from __future__ import annotations

import contextvars
import functools
import json
import logging
import operator
from dataclasses import dataclass, replace
from typing import Annotated, Any, Awaitable, Callable, Iterable, Mapping, Sequence, TypedDict

from corpai.types import Level, Principal

log = logging.getLogger(__name__)

CAPS: frozenset[str] = frozenset({"untrusted_input", "sensitive_data", "external_action"})

LOCAL_MODE_NOTICE = {"t": "notice", "code": "local_mode", "zh": "本会话已切换到本地模式",
                     "en": "This session now uses the local model"}

_MAX_TRACE_INPUT = 4000  # tool-argument text kept for the (masked) trace input


# ---- context and state ------------------------------------------------------------------------

@dataclass(frozen=True)
class Services:
    gateway: Any  # corpai.gateway.ModelGateway
    mcp: Any  # corpai.mcp_client.McpClient
    tracer: Any  # corpai.trace.Tracer


@dataclass(frozen=True)
class RunContext:
    """Passed as `context=` on every invoke and resume; built by code from the session, never by a model."""

    principal: Principal
    run_id: str
    thread_id: str
    agent: str
    services: Services
    user_level: Level = Level.L1  # the user may raise the session's level, never lower it


def max_level(a: Any, b: Any) -> int:
    """hwm reducer. Anything that is not a level counts as L4 (fail closed); 0 is the unset channel."""
    return max(_as_int_level(a, allow_zero=True), _as_int_level(b, allow_zero=True))


def _as_int_level(v: Any, *, allow_zero: bool = False) -> int:
    if v is None:
        return 0 if allow_zero else 1
    if isinstance(v, bool):
        return 4
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 4
    if allow_zero and n == 0:
        return 0
    return n if 1 <= n <= 4 else 4


class BaseState(TypedDict, total=False):
    sensitivity_hwm: Annotated[int, max_level]
    events: Annotated[list[dict], operator.add]  # route badges, policy notes, citations for the UI


def current_hwm(state: Mapping[str, Any]) -> Level:
    """The session's hwm as a Level; an unset channel means L1 (nothing has entered yet)."""
    return Level(max(1, _as_int_level(state.get("sensitivity_hwm"), allow_zero=True)))


def raise_hwm(state: Mapping[str, Any], level: Level | int, reason: str) -> dict:
    """The update that raises hwm to `level`, with an `hwm` event when it actually goes up and the
    one-time `local_mode` notice when it first reaches L3 (from then on every model call is local)."""
    before = _as_int_level(state.get("sensitivity_hwm"), allow_zero=True)
    new = _as_int_level(level)
    events: list[dict] = []
    if new > max(before, 1):
        events.append({"t": "hwm", "from": max(before, 1), "to": new, "reason": reason})
        if before < Level.L3 <= new:
            events.append(dict(LOCAL_MODE_NOTICE))
    return {"sensitivity_hwm": max(before, new, 1), "events": events}


def merge_updates(*updates: Mapping[str, Any]) -> dict:
    """Combine helper updates: hwm by max, events concatenated, other keys last-wins."""
    out: dict[str, Any] = {"events": []}
    hwm = 0
    for u in updates:
        for k, v in u.items():
            if k == "sensitivity_hwm":
                hwm = max_level(hwm, v)
            elif k == "events":
                out["events"] = out["events"] + list(v or [])
            else:
                out[k] = v
    if hwm:
        out["sensitivity_hwm"] = hwm
    return out


# ---- published content (ChatRequest.public_only) ---------------------------------------------

class PublicText(str):
    """Text that runtime code knows to be published content (public web pages, the public site,
    the GDPR library) or a constant prompt written in code. Any string operation returns a plain
    str, so mixing in anything else silently drops the mark (fail closed)."""

    __slots__ = ()


def published(text: str) -> PublicText:
    """Mark `text` as published content. Runtime code only: never call it on model output, user
    input, CRM rows or anything else that was not published by its owner."""
    if not isinstance(text, str):
        raise TypeError("published() takes a string")
    return PublicText(text)


def _all_published(messages: Sequence[Mapping[str, Any]]) -> bool:
    if not messages:
        return False
    for m in messages:
        if not isinstance(m, Mapping) or set(m) - {"role", "content"}:
            return False  # tool calls, names, … are not published content
        content = m.get("content")
        parts = content if isinstance(content, list) else [content]
        for part in parts:
            text = part.get("text") if isinstance(part, Mapping) and part.get("type") == "text" else part
            if type(text) is not PublicText:  # noqa: E721 - a subclass check would accept str lookalikes
                return False
    return True


# ---- trace parent ---------------------------------------------------------------------------

_parent_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("corpai_parent_span", default=None)
# True while a gate node runs: it may only wait for the person (approval_gate). LangGraph re-runs
# an interrupted node from the top on resume, so work placed in it would run twice, and the
# Rule-of-Two walker treats it as holding nothing.
_in_gate: contextvars.ContextVar[bool] = contextvars.ContextVar("corpai_in_gate_node", default=False)


def _no_gate(helper: str) -> None:
    if _in_gate.get():
        raise RuntimeError(f"{helper}() inside a gate node: a gate node only calls approval_gate")


def _ctx(rt: Any) -> RunContext:
    ctx = getattr(rt, "context", None)
    if not isinstance(ctx, RunContext):
        # A graph started without a RunContext has no principal: refuse instead of guessing.
        raise RuntimeError("graph run without a RunContext")
    return ctx


# ---- helpers ----------------------------------------------------------------------------------

def _route_reason(route: str, level: int) -> str:
    # ChatResult carries route and level, not the gateway's audit reason; the two cases it can be
    # are recoverable: local at L1/L2 only happens as the external_down fallback.
    if route == "local" and level <= Level.L2:
        return "external_down_fallback"
    return "route_by_level"


def llm_update(state: Mapping[str, Any], result: Any) -> dict:
    """The update after a model call: a route event, and hwm raised to the gateway's level (the
    gateway classifies the whole payload, so it may see more than the hwm said)."""
    route, level = str(result.route), int(result.level)
    upd = raise_hwm(state, level, "model_input")
    upd["events"] = [{"t": "route", "route": route, "level": level, "reason": _route_reason(route, level)}] + \
        upd["events"]
    return upd


async def llm(rt: Any, state: Mapping[str, Any], messages: list[dict], *, purpose: str,
              json_mode: bool = False, max_tokens: int = 1200, temperature: float = 0.2,
              tools: list[dict] | None = None, public_only: bool = False) -> Any:
    """One model call through the gateway with hwm from the state. The route is never chosen here.

    public_only is passed on only while hwm is L1 and every message part is PublicText; otherwise
    it is dropped silently (the gateway then classifies normally, which is the safe direction)."""
    from corpai.gateway import ChatRequest

    _no_gate("llm")
    ctx = _ctx(rt)
    hwm = current_hwm(state)
    po = bool(public_only) and hwm == Level.L1 and _all_published(messages)
    req = ChatRequest(messages=messages, principal=ctx.principal, hwm=hwm, purpose=purpose, run_id=ctx.run_id,
                      agent=ctx.agent, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature,
                      tools=tools, public_only=po)
    return await ctx.services.gateway.chat(req)


def _tool_event(domain: str, tool: str, env: Any) -> dict:
    # §18 / UI shape: [{id, reason}]. Reasons are the servers' fixed texts ("not found or no
    # permission"), never row content.
    denied = [{"id": str(d.get("id")), "reason": str(d.get("reason") or "")[:200]}
              for d in env.denied[:20] if isinstance(d, Mapping)]
    ev: dict[str, Any] = {"t": "tool", "domain": domain, "tool": tool,
                          "decision": "deny" if env.error == "tool_denied" else "allow",
                          "level": int(env.level), "ok": bool(env.ok), "denied": denied}
    if env.error:
        ev["error"] = env.error
    return ev


async def call_tool(rt: Any, state: Mapping[str, Any], domain: str, tool: str,
                    args: Mapping[str, Any] | None = None) -> tuple[Any, dict]:
    """Call one MCP tool as the run's principal through the run's agent (fresh one-tool token per
    call, minted inside McpClient; the token never reaches this function's caller)."""
    _no_gate("call_tool")
    ctx = _ctx(rt)
    hwm = current_hwm(state)
    args = dict(args or {})
    try:
        arg_text = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)[:_MAX_TRACE_INPUT]
    except (TypeError, ValueError):
        arg_text = None
    async with ctx.services.tracer.span(f"tool:{domain}.{tool}", kind="tool", run_id=ctx.run_id,
                                        principal=ctx.principal, level=hwm, parent_id=_parent_span.get(),
                                        input=arg_text) as sp:
        env = await ctx.services.mcp.call(principal=ctx.principal, agent=ctx.agent, run_id=ctx.run_id,
                                          domain=domain, tool=tool, args=args)
        carries_data = env.ok or env.data is not None
        if carries_data:
            sp.set_level(env.level)
        sp.set_attr("ok", bool(env.ok))
        sp.set_attr("decision", "deny" if env.error == "tool_denied" else "allow")
        if env.error:
            sp.set_attr("error", str(env.error)[:64])
        sp.set_attr("denied", len(env.denied))
        sp.set_attr("sources", len(env.sources))
    events = [_tool_event(domain, tool, env)]
    upd = {"events": events}
    # A data-less failure (denied, bad arguments, transport) brought nothing into the session, so
    # it does not raise the hwm; ToolEnvelope.failure labels those L4 only to keep label readers safe.
    if carries_data:
        upd = merge_updates(upd, raise_hwm(state, env.level, f"tool:{domain}.{tool}"))
    if env.proposal_id:
        upd["events"].append({"t": "approval", "proposal_id": env.proposal_id, "tier": env.tier})
    return env, upd


async def retrieve(rt: Any, state: Mapping[str, Any], query: str, *, purpose: str, k: int = 8,
                   collections: Iterable[str] | None = None,
                   keep: Callable[[Any], bool] | None = None) -> tuple[Any, dict]:
    """corpai.rag.retrieve as the run's principal (policy filter + RLS). `keep` lets a graph drop
    hits it judges irrelevant *before* they enter the session: dropped hits are not returned, not
    put in state and do not raise the hwm."""
    from corpai.rag import retrieve as rag_retrieve

    _no_gate("retrieve")
    ctx = _ctx(rt)
    hwm = current_hwm(state)
    async with ctx.services.tracer.span(f"retrieve:{purpose}", kind="retrieval", run_id=ctx.run_id,
                                        principal=ctx.principal, level=hwm, parent_id=_parent_span.get(),
                                        input=query) as sp:
        res = await rag_retrieve(ctx.principal, query, purpose=purpose, k=k, collections=collections,
                                 run_id=ctx.run_id, agent=ctx.agent)
        if keep is not None:
            hits = [h for h in res.hits if keep(h)]
            level = max((h.level for h in hits), default=Level.L1)
            res = replace(res, hits=hits, level=Level(level))
        sp.set_level(res.level)
        sp.set_attr("count", len(res.hits))
        sp.set_attr("collections", sorted(res.collections))
        sp.set_output(" ".join(h.doc_id for h in res.hits))
    ev = {"t": "retrieval", "count": len(res.hits), "level": int(res.level),
          "collections": sorted(res.collections)}
    upd = merge_updates({"events": [ev]}, raise_hwm(state, res.level, f"retrieval:{purpose}"))
    return res, upd


async def approval_gate(rt: Any, proposal_id: str) -> dict:
    """Pause the graph until a person decides on `proposal_id` (LangGraph interrupt). The API
    resumes the thread with Command(resume=...) built by code from the stored proposal, never from
    the request body. A malformed or mismatched resume value counts as not approved.

    Returns {proposal_id, approved, executed, status, result}: `approved` (= `executed`) is True
    only when the approved action actually ran; an approval whose executor refused or failed comes
    back approved=False with status "failed" and the executor's result, so branch on `approved`."""
    from langgraph.types import interrupt

    ctx = _ctx(rt)
    value = interrupt({"type": "approval", "proposal_id": proposal_id})
    ok = isinstance(value, Mapping) and value.get("proposal_id") == proposal_id
    decision = dict(value) if ok else {"proposal_id": proposal_id, "approved": False, "status": "invalid_resume"}
    decision["approved"] = bool(decision.get("approved")) and ok
    async with ctx.services.tracer.span("approval", kind="policy", run_id=ctx.run_id, principal=ctx.principal,
                                        level=Level.L1, parent_id=_parent_span.get()) as sp:
        sp.set_attr("proposal_id", proposal_id)
        sp.set_attr("approved", decision["approved"])
        sp.set_attr("status", str(decision.get("status"))[:32])
    return decision


# ---- Step: one node's helpers with a shared, growing update -----------------------------------

class Step:
    """Per-node accumulator. `get()` reads like the state but sees the hwm raised by earlier calls
    in the same node, so two tool calls in a row do not both claim the first hwm change."""

    def __init__(self, rt: Any, state: Mapping[str, Any]):
        self.rt = rt
        self._state = state
        self._hwm = _as_int_level(state.get("sensitivity_hwm"), allow_zero=True)
        self._events: list[dict] = []

    def get(self, key: str, default: Any = None) -> Any:
        if key == "sensitivity_hwm":
            return self._hwm
        return self._state.get(key, default)

    def __getitem__(self, key: str) -> Any:
        if key == "sensitivity_hwm":
            return self._hwm
        return self._state[key]

    @property
    def hwm(self) -> Level:
        return Level(max(1, self._hwm))

    def apply(self, update: Mapping[str, Any]) -> None:
        if "sensitivity_hwm" in update:
            self._hwm = max_level(self._hwm, update["sensitivity_hwm"])
        self._events.extend(update.get("events") or [])

    def event(self, ev: dict) -> None:
        self._events.append(ev)

    def raise_to(self, level: Level | int, reason: str) -> None:
        self.apply(raise_hwm(self, level, reason))

    async def retrieve(self, query: str, **kw: Any) -> Any:
        res, upd = await retrieve(self.rt, self, query, **kw)
        self.apply(upd)
        return res

    async def call_tool(self, domain: str, tool: str, args: Mapping[str, Any] | None = None) -> Any:
        env, upd = await call_tool(self.rt, self, domain, tool, args)
        self.apply(upd)
        return env

    async def llm(self, messages: list[dict], **kw: Any) -> Any:
        res = await llm(self.rt, self, messages, **kw)
        self.apply(llm_update(self, res))
        return res

    def update(self, **values: Any) -> dict:
        out = dict(values)
        out["events"] = list(self._events)
        if self._hwm:
            out["sensitivity_hwm"] = self._hwm
        return out


# ---- @node: capabilities and a node span --------------------------------------------------------

NodeFn = Callable[[Any, Any], Awaitable[Any]]


def node(*, caps: Iterable[str], gate: bool = False, name: str | None = None) -> Callable[[NodeFn], NodeFn]:
    """Declare a node's Rule-of-Two capabilities ⊆ CAPS ("untrusted_input", "sensitive_data",
    "external_action"); gate=True marks a node that calls approval_gate (a person decides before
    anything after it runs). A gate node declares no capabilities and may not call llm,
    call_tool or retrieve (enforced at run time). The node runs inside a `node` trace span;
    helpers nest under it."""
    declared = frozenset(caps)
    unknown = declared - CAPS
    if unknown:
        raise ValueError(f"unknown capabilities {sorted(unknown)}; allowed: {sorted(CAPS)}")
    if gate and declared:
        raise ValueError("a gate node only waits for the approval: declare caps=set() and do the work "
                         "in the nodes before and after it")

    def deco(fn: NodeFn) -> NodeFn:
        span_name = f"node:{name or fn.__name__}"

        @functools.wraps(fn)
        async def wrapper(state, runtime):
            from langgraph.errors import GraphBubbleUp

            ctx = _ctx(runtime)
            bubble: BaseException | None = None
            out: Any = None
            async with ctx.services.tracer.span(span_name, kind="node", run_id=ctx.run_id, principal=ctx.principal,
                                                level=current_hwm(state), parent_id=_parent_span.get()) as sp:
                token = _parent_span.set(sp.span_id)
                gate_token = _in_gate.set(bool(gate))
                try:
                    out = await fn(state, runtime)
                except GraphBubbleUp as e:  # an interrupt is a pause, not a failure of the node
                    sp.set_attr("interrupted", True)
                    bubble = e
                finally:
                    _in_gate.reset(gate_token)
                    _parent_span.reset(token)
                if isinstance(out, Mapping) and "sensitivity_hwm" in out:
                    sp.set_level(_as_int_level(out["sensitivity_hwm"]))
                sp.set_attr("caps", sorted(declared))
            if bubble is not None:
                raise bubble
            return out

        wrapper.__corpai_caps__ = declared  # type: ignore[attr-defined]
        wrapper.__corpai_gate__ = bool(gate)  # type: ignore[attr-defined]
        return wrapper

    return deco


def node_caps(fn: Any) -> tuple[frozenset[str], bool] | None:
    """(caps, gate) declared with @node, or None for an undeclared callable."""
    for obj in (fn, getattr(fn, "afunc", None), getattr(fn, "func", None)):
        if obj is not None and hasattr(obj, "__corpai_caps__"):
            return frozenset(obj.__corpai_caps__), bool(getattr(obj, "__corpai_gate__", False))
    return None
