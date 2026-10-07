"""Runtime helpers (architecture §12): the hwm only goes up, the route is never chosen by a graph,
public_only is honoured only for published text at L1, denials are explicit, the RunContext never
reaches a checkpoint, and graphs refuse to run without one."""

from __future__ import annotations

import operator
from types import SimpleNamespace
from typing import Annotated

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from corpai.agents import (
    BaseState,
    RunContext,
    Step,
    approval_gate,
    call_tool,
    current_hwm,
    llm,
    max_level,
    node,
    published,
    raise_hwm,
)
from corpai.agents import runtime
from corpai.mcp_client import ToolEnvelope
from corpai.policy import RagFilter
from corpai.rag import Hit, RetrievalResult
from corpai.types import Level, Trust
from tests.agents.fakes import FakeGateway, FakeMcp, NullTracer, context

pytestmark = pytest.mark.anyio


def rt(ctx):
    return SimpleNamespace(context=ctx)


def env(level=3, *, ok=True, data=None, **kw):
    return ToolEnvelope(ok=ok, data={"x": 1} if data is None and ok else data,
                        label={"level": level, "trust": "internal", "sources": [f"crm.account:A-000{level}"]}, **kw)


# ---- reducer and hwm events -------------------------------------------------------------------

@pytest.mark.parametrize("a,b,expected", [(1, 3, 3), (3, 1, 3), (0, 2, 2), (0, None, 0), (2, "junk", 4),
                                          (2, 9, 4), (2, -1, 4), (1, True, 4)])
def test_max_level_is_monotone_and_fails_closed(a, b, expected):
    assert max_level(a, b) == expected


async def test_hwm_never_goes_down_in_a_graph():
    @node(caps=set())
    async def up(state, runtime):
        return {"sensitivity_hwm": 3}

    @node(caps=set())
    async def down(state, runtime):
        return {"sensitivity_hwm": 1}

    g = StateGraph(BaseState, context_schema=RunContext)
    g.add_node("up", up)
    g.add_node("down", down)
    g.add_edge(START, "up")
    g.add_edge("up", "down")
    g.add_edge("down", END)
    out = await g.compile().ainvoke({}, context=context())
    assert out["sensitivity_hwm"] == 3


def test_raise_hwm_emits_one_local_mode_notice():
    step = Step(None, {})
    for lvl in (2, 3, 2, 4):
        step.raise_to(lvl, f"r{lvl}")
    upd = step.update()
    assert upd["sensitivity_hwm"] == 4
    assert [(e["from"], e["to"]) for e in upd["events"] if e["t"] == "hwm"] == [(1, 2), (2, 3), (3, 4)]
    assert [e["code"] for e in upd["events"] if e["t"] == "notice"] == ["local_mode"]


def test_raise_hwm_does_nothing_when_not_higher():
    assert raise_hwm({"sensitivity_hwm": 3}, 2, "x") == {"sensitivity_hwm": 3, "events": []}
    assert current_hwm({}) == Level.L1 and current_hwm({"sensitivity_hwm": "bad"}) == Level.L4


# ---- llm ------------------------------------------------------------------------------------

async def test_llm_sends_the_state_hwm_and_never_a_route():
    gw = FakeGateway()
    ctx = context(gateway=gw)
    res = await llm(rt(ctx), {"sensitivity_hwm": 3}, [{"role": "user", "content": "hi"}], purpose="qa.answer")
    req = gw.requests[0]
    assert req.hwm == Level.L3 and res.route == "local"
    assert not hasattr(req, "route")  # the gateway decides; a caller cannot even say
    assert req.principal is ctx.principal and req.agent == "qa" and req.run_id == ctx.run_id


async def test_step_llm_records_route_and_raises_hwm_to_the_gateway_level():
    gw = FakeGateway(content_level=Level.L3)  # the gateway found a name the hwm did not know about
    step = Step(rt(context(gateway=gw)), {"sensitivity_hwm": 1})
    await step.llm([{"role": "user", "content": "x"}], purpose="qa.answer")
    upd = step.update()
    assert upd["sensitivity_hwm"] == 3
    assert {"t": "route", "route": "local", "level": 3, "reason": "route_by_level"} in upd["events"]
    assert any(e.get("code") == "local_mode" for e in upd["events"])


@pytest.mark.parametrize("hwm,messages,expected", [
    (1, [{"role": "system", "content": published("Summarise.")}, {"role": "user", "content": published("Page")}], True),
    (2, [{"role": "user", "content": published("Page")}], False),  # internal data already in the session
    (1, [{"role": "user", "content": "Page"}], False),  # plain str: not known to be published
    (1, [{"role": "user", "content": published("Page") + " and more"}], False),  # mixing drops the mark
    (1, [{"role": "user", "content": [{"type": "text", "text": published("a")}, {"type": "text", "text": "b"}]}],
     False),
    (1, [{"role": "assistant", "content": published("a"), "tool_calls": []}], False),
    (1, [], False),
])
async def test_public_only_only_for_published_text_at_l1(hwm, messages, expected):
    gw = FakeGateway()
    await llm(rt(context(gateway=gw)), {"sensitivity_hwm": hwm}, messages, purpose="sales.web_extract",
              public_only=True)
    assert gw.requests[0].public_only is expected


async def test_public_only_is_never_set_unless_asked():
    gw = FakeGateway()
    await llm(rt(context(gateway=gw)), {}, [{"role": "user", "content": published("x")}], purpose="qa.answer")
    assert gw.requests[0].public_only is False


# ---- call_tool --------------------------------------------------------------------------------

async def test_tool_data_raises_hwm_and_is_traced_under_the_node():
    tracer = NullTracer()
    mcp = FakeMcp([env(3)])
    ctx = context(mcp=mcp, tracer=tracer)
    e, upd = await call_tool(rt(ctx), {"sensitivity_hwm": 1}, "crm", "get_account", {"account_id": "A-0002"})
    assert upd["sensitivity_hwm"] == 3
    tool_ev = upd["events"][0]
    assert tool_ev == {"t": "tool", "domain": "crm", "tool": "get_account", "decision": "allow", "level": 3,
                       "ok": True, "denied": []}
    assert mcp.calls[0]["principal"] is ctx.principal and mcp.calls[0]["agent"] == "qa"
    assert mcp.calls[0]["run_id"] == ctx.run_id
    assert tracer.spans[0].kind == "tool" and tracer.spans[0].level == Level.L3


async def test_a_denied_tool_is_an_explicit_event_and_brings_nothing_in():
    denied = ToolEnvelope.failure("tool_denied", "no permission", denied=[{"id": "crm.get_account", "reason": "x"}])
    _e, upd = await call_tool(rt(context(mcp=FakeMcp([denied]))), {"sensitivity_hwm": 1}, "crm", "get_account", {})
    assert upd.get("sensitivity_hwm", 1) == 1  # no data entered the session
    ev = upd["events"][0]
    assert ev["decision"] == "deny" and ev["denied"] == [{"id": "crm.get_account", "reason": "x"}] and ev["error"] == "tool_denied"


async def test_tool_data_without_a_label_counts_as_l4():
    unlabelled = ToolEnvelope.from_dict({"ok": True, "data": {"rows": [1]}})
    _e, upd = await call_tool(rt(context(mcp=FakeMcp([unlabelled]))), {}, "docs", "search_docs", {"query": "x"})
    assert upd["sensitivity_hwm"] == 4


async def test_a_proposal_envelope_adds_an_approval_event():
    prop = env(3, proposal_id="prop_x", status="pending", tier=3)
    _e, upd = await call_tool(rt(context(mcp=FakeMcp([prop]))), {}, "crm", "send_email", {})
    assert {"t": "approval", "proposal_id": "prop_x", "tier": 3} in upd["events"]


# ---- retrieve ---------------------------------------------------------------------------------

def _hit(doc_id, level, text):
    return Hit(chunk_id=f"{doc_id}@v1#0", doc_id=doc_id, version=1, title=doc_id, heading="", text=text,
               level=Level(level), trust=Trust.INTERNAL, collection="general", score=0.1, source_uri="")


async def test_dropped_hits_do_not_raise_the_hwm(monkeypatch):
    import corpai.rag

    async def fake_retrieve(p, query, *, purpose, k, collections, run_id, agent):
        hits = [_hit("pub", 1, "FleetPilot plans routes"), _hit("board", 4, "unrelated board minutes")]
        return RetrievalResult(hits=hits, level=Level.L4, filter=RagFilter(frozenset(), Level.L3, frozenset(), None),
                               collections=frozenset({"public", "exec"}))

    monkeypatch.setattr(corpai.rag, "retrieve", fake_retrieve)
    ctx = context()
    res, upd = await runtime.retrieve(rt(ctx), {}, "FleetPilot", purpose="qa",
                                      keep=lambda h: "FleetPilot" in h.text)
    assert [h.doc_id for h in res.hits] == ["pub"] and res.level == Level.L1
    assert upd["sensitivity_hwm"] == 1
    assert upd["events"][0] == {"t": "retrieval", "count": 1, "level": 1, "collections": ["exec", "public"]}
    res, upd = await runtime.retrieve(rt(ctx), {}, "FleetPilot", purpose="qa")  # no filter: everything counts
    assert upd["sensitivity_hwm"] == 4


# ---- nodes, context and checkpoints -----------------------------------------------------------------

def test_node_rejects_unknown_capabilities():
    with pytest.raises(ValueError):
        node(caps={"sensitive_data", "network"})


class _S(BaseState, total=False):
    proposal: dict
    decision: dict
    log: Annotated[list, operator.add]


async def test_a_graph_refuses_to_run_without_a_run_context():
    @node(caps=set())
    async def a(state, runtime):
        return {"log": ["ran"]}

    g = StateGraph(_S, context_schema=RunContext)
    g.add_node("a", a)
    g.add_edge(START, "a")
    with pytest.raises(RuntimeError, match="RunContext"):
        await g.compile().ainvoke({})


def _gate_graph():
    @node(caps={"sensitive_data"})
    async def propose(state, runtime):
        return {"proposal": {"id": "prop_aaaaaaaaaaaaaaaaaaaa"}, "sensitivity_hwm": 3}

    @node(caps=set(), gate=True)
    async def gate(state, runtime):
        return {"decision": await approval_gate(runtime, state["proposal"]["id"])}

    g = StateGraph(_S, context_schema=RunContext)
    g.add_node("propose", propose)
    g.add_node("gate", gate)
    g.add_edge(START, "propose")
    g.add_edge("propose", "gate")
    g.add_edge("gate", END)
    return g


async def test_approval_gate_pauses_and_a_mismatched_resume_is_not_an_approval():
    saver = InMemorySaver()
    graph = _gate_graph().compile(checkpointer=saver)
    tracer = NullTracer()
    ctx = context(tracer=tracer)
    cfg = {"configurable": {"thread_id": ctx.thread_id}}
    await graph.ainvoke({}, cfg, context=ctx)
    snap = await graph.aget_state(cfg)
    assert [i.value for i in snap.interrupts] == [{"type": "approval", "proposal_id": "prop_aaaaaaaaaaaaaaaaaaaa"}]
    node_span = next(s for s in tracer.spans if s.name == "node:gate")
    assert node_span.attrs.get("interrupted") is True  # a pause is not recorded as a failure
    out = await graph.ainvoke(Command(resume={"proposal_id": "prop_other", "approved": True}), cfg, context=ctx)
    assert out["decision"]["approved"] is False and out["decision"]["status"] == "invalid_resume"


async def test_approval_gate_returns_the_decision():
    graph = _gate_graph().compile(checkpointer=InMemorySaver())
    ctx = context()
    cfg = {"configurable": {"thread_id": ctx.thread_id}}
    await graph.ainvoke({}, cfg, context=ctx)
    out = await graph.ainvoke(Command(resume={"proposal_id": "prop_aaaaaaaaaaaaaaaaaaaa", "approved": True,
                                              "status": "executed"}), cfg, context=ctx)
    assert out["decision"]["approved"] is True and out["sensitivity_hwm"] == 3


async def test_run_context_never_reaches_a_checkpoint():
    saver = InMemorySaver()
    graph = _gate_graph().compile(checkpointer=saver)
    ctx = context()
    cfg = {"configurable": {"thread_id": ctx.thread_id}}
    await graph.ainvoke({}, cfg, context=ctx)
    stored = repr(saver.storage) + repr(saver.writes) + repr(getattr(saver, "blobs", {}))
    assert "ctx-marker-7f3a" not in stored and "grp-marker-91c2" not in stored
    assert ctx.principal.user_id not in stored


async def test_helper_spans_nest_under_the_node_span():
    tracer = NullTracer()

    @node(caps={"sensitive_data"})
    async def fetch(state, runtime):
        step = Step(runtime, state)
        await step.call_tool("crm", "get_account", {"account_id": "A-0002"})
        return step.update()

    g = StateGraph(BaseState, context_schema=RunContext)
    g.add_node("fetch", fetch)
    g.add_edge(START, "fetch")
    out = await g.compile().ainvoke({}, context=context(tracer=tracer, mcp=FakeMcp([env(3)])))
    node_span = next(s for s in tracer.spans if s.kind == "node")
    tool_span = next(s for s in tracer.spans if s.kind == "tool")
    assert tool_span.parent_id == node_span.span_id
    assert node_span.level == Level.L3 and out["sensitivity_hwm"] == 3
    assert node_span.attrs["caps"] == ["sensitive_data"]


async def test_a_gate_node_cannot_call_tools_or_the_model():
    """Review fix: a gate node only waits. LangGraph re-runs it from the top on resume, so a tool call
    placed before approval_gate would run twice, and the Rule-of-Two walker counts it as holding nothing."""
    @node(caps=set(), gate=True)
    async def sneaky(state, runtime):
        await call_tool(runtime, state, "crm", "send_email", {"to": "x@y.example"})
        return {}

    g = StateGraph(_S, context_schema=RunContext)
    g.add_node("sneaky", sneaky)
    g.add_edge(START, "sneaky")
    mcp = FakeMcp([env(3)])
    with pytest.raises(RuntimeError, match="gate node"):
        await g.compile().ainvoke({}, context=context(mcp=mcp))
    assert mcp.calls == []  # refused before any MCP call


def test_resume_value_is_approved_only_when_executed():
    from corpai.agents._runner import resume_value

    def prop(status, result=None):
        return SimpleNamespace(id="prop_aaaaaaaaaaaaaaaaaaaa", status=status, result=result)

    assert resume_value(prop("executed", {"outbox_id": "OB-1"}))["approved"] is True
    failed = resume_value(prop("failed", {"error": "recipient_not_contact"}))
    assert failed["approved"] is False and failed["executed"] is False and failed["status"] == "failed"
    assert failed["result"] == {"error": "recipient_not_contact"}
    for status in ("rejected", "expired", "approved"):
        assert resume_value(prop(status))["approved"] is False


async def test_approval_gate_reports_a_failed_execution_as_not_approved():
    from corpai.agents._runner import resume_value

    graph = _gate_graph().compile(checkpointer=InMemorySaver())
    ctx = context()
    cfg = {"configurable": {"thread_id": ctx.thread_id}}
    await graph.ainvoke({}, cfg, context=ctx)
    failed = SimpleNamespace(id="prop_aaaaaaaaaaaaaaaaaaaa", status="failed", result={"error": "executor_error"})
    out = await graph.ainvoke(Command(resume=resume_value(failed)), cfg, context=ctx)
    assert out["decision"]["approved"] is False and out["decision"]["status"] == "failed"
