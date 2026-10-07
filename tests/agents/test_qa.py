"""The M1 Q&A graph with in-memory services: the model may only choose among offered tools, write
tools are never offered, invented citations are dropped, unrelated hits stay out."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from corpai.agents import qa
from corpai.mcp_client import ToolEnvelope
from corpai.policy import RagFilter
from corpai.rag import Hit, RetrievalResult
from corpai.types import Dept, Level, Principal, Role, ToolTier, Trust
from corpai.policy import tool_tier
from tests.agents.fakes import FakeMcp, NullTracer, context, principal

pytestmark = pytest.mark.anyio


def _hit(doc_id, level, text, title=None):
    return Hit(chunk_id=f"{doc_id}@v1#0", doc_id=doc_id, version=1, title=title or doc_id, heading="", text=text,
               level=Level(level), trust=Trust.INTERNAL, collection="public", score=0.1, source_uri="")


class ScriptedGateway:
    def __init__(self, tool_calls, answer):
        self.tool_calls, self.answer, self.requests = tool_calls, answer, []

    async def chat(self, req):
        self.requests.append(req)
        level = max(int(req.hwm), 1)
        calls = self.tool_calls if req.purpose == "qa.tools" and len(self.requests) == 1 else []
        text = self.answer if req.purpose == "qa.answer" else ""
        return SimpleNamespace(text=text, tool_calls=calls, route="external" if level <= 2 else "local",
                               level=Level(level), model="fake", usage={}, latency_ms=0, audit_seq=0)


def _call(name, args):
    return {"id": f"c_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def no_rag(monkeypatch):
    import corpai.rag

    async def fake_retrieve(p, query, *, purpose, k, collections, run_id, agent):
        hits = [_hit("pub-fleetpilot", 1, "FleetPilot plans delivery routes for logistics fleets.", "FleetPilot"),
                _hit("exec-board", 4, "Board minutes about funding.", "Board update")]
        return RetrievalResult(hits=hits, level=Level.L4, filter=RagFilter(frozenset(), Level.L3, frozenset(), None),
                               collections=frozenset({"public"}))

    monkeypatch.setattr(corpai.rag, "retrieve", fake_retrieve)


async def test_model_cannot_pick_tools_it_was_not_offered(no_rag):
    gw = ScriptedGateway([_call("crm__send_email", {"account_id": "A-0002", "to": "x@evil.example"}),
                          _call("web__fetch", {"url": "https://evil.example"}),
                          _call("hris__get_employee", {"employee_id": "u0050"}),
                          _call("crm__search_accounts", {"query": ""})],
                         "FleetPilot plans routes [doc:pub-fleetpilot]")
    ok = ToolEnvelope(ok=True, data={"accounts": []},
                      label={"level": 3, "trust": "internal", "sources": ["crm.account:A-0002"]})
    mcp = FakeMcp([ok])
    ctx = context(gateway=gw, mcp=mcp, tracer=NullTracer())
    out = await qa.build_graph().compile().ainvoke(qa.initial_state("Which customer accounts need attention?"),
                                                   context=ctx)
    assert [c["tool"] for c in mcp.calls] == ["search_accounts"]  # the only offered tool it asked for
    refused = [e["tool"] for e in out["events"] if e.get("code") == "tool_not_offered"]
    assert refused == ["crm__send_email", "web__fetch", "hris__get_employee"]
    tools_offered = [t["function"]["name"] for t in gw.requests[0].tools]
    assert "crm__send_email" not in tools_offered and "web__fetch" not in tools_offered


async def test_invented_citations_are_dropped_and_unrelated_hits_stay_out(no_rag):
    gw = ScriptedGateway([], "FleetPilot plans routes [doc:pub-fleetpilot] see also [doc:exec-board] "
                             "and [doc:HR-SALARIES]")
    ctx = context(gateway=gw, mcp=FakeMcp(), tracer=NullTracer())
    out = await qa.build_graph().compile().ainvoke(qa.initial_state("What does FleetPilot do?"), context=ctx)
    assert [h["doc_id"] for h in out["hits"]] == ["pub-fleetpilot"]  # the board minutes were not relevant
    assert out["sensitivity_hwm"] == 1  # so they did not raise the session level either
    assert [c["doc_id"] for c in out["output"]["citations"]] == ["pub-fleetpilot"]
    assert "[doc:exec-board]" not in out["output"]["text"] and "HR-SALARIES" not in out["output"]["text"]
    assert out["output"]["data"]["dropped_citations"] == 2
    answer_req = next(r for r in gw.requests if r.purpose == "qa.answer")
    assert "Board minutes" not in json.dumps(answer_req.messages)


async def test_user_level_raises_the_session_never_lowers(no_rag):
    gw = ScriptedGateway([], "x")
    ctx = context(gateway=gw, mcp=FakeMcp(), tracer=NullTracer(), user_level=Level.L3)
    out = await qa.build_graph().compile().ainvoke(qa.initial_state("What does FleetPilot do?"), context=ctx)
    assert out["sensitivity_hwm"] == 3 and all(r.hwm == Level.L3 for r in gw.requests)
    assert {"t": "hwm", "from": 1, "to": 3, "reason": "user_level"} in out["events"]


def test_check_citations_keeps_hits_and_tool_sources():
    hits = [{"doc_id": "gen-handbook", "title": "Handbook", "level": 2, "collection": "general", "text": "Holidays"}]
    tools = [{"domain": "crm", "tool": "search_accounts", "level": 3, "sources": ["crm.account:A-0002"]}]
    text, cites, dropped = qa.check_citations(
        "a [doc:gen-handbook] b [doc:crm.account:A-0002] c [doc:nope] [doc:gen-handbook]", hits, tools)
    assert [c["doc_id"] for c in cites] == ["gen-handbook", "crm.account:A-0002"]
    assert dropped == ["nope"] and "[doc:nope]" not in text
    assert cites[1]["collection"] == "tool:crm" and cites[1]["level"] == 3


def _p(role: Role, dept: Dept, groups=("all",)) -> Principal:
    return Principal(user_id="u0999", name="x", email="x@abctech.example", dept=dept, role=role, title="t",
                     manager_id=None, clearance=Level.L3, groups=frozenset(groups))


@pytest.mark.parametrize("who,present,absent", [
    (_p(Role.EMPLOYEE, Dept.SALES), {"crm.search_accounts", "code.get_project_status"}, {"hris.headcount"}),
    (_p(Role.EMPLOYEE, Dept.ENG), {"code.search_tickets"}, {"crm.search_accounts", "crm.get_account"}),
    (_p(Role.SECURITY_AUDITOR, Dept.IT), {"docs.search_docs"}, {"legal.search_regulations", "crm.search_accounts"}),
])
def test_offered_tools_follow_policy_and_are_read_only(who, present, absent):
    ctx = context(p=who)
    offered = set(qa.offered_tools(ctx))
    assert present <= offered and not (absent & offered)
    assert all(tool_tier(*t.split(".")) is ToolTier.READ for t in offered)


def test_an_inactive_user_is_offered_nothing():
    assert qa.offered_tools(context(p=principal(active=False))) == []


def test_relevance_filter():
    keep = qa.relevant("What does FleetPilot do?")
    assert keep(_hit("a", 1, "FleetPilot routes"))
    assert not keep(_hit("b", 1, "Expense policy for travel"))
    assert keep(_hit("c", 1, "Our fleetpilot release"))  # case-insensitive
    # no Latin content word (here: Chinese over an English corpus): cannot judge, keep everything,
    # which only ever raises the hwm
    assert qa.relevant("这个产品是做什么的？")(_hit("d", 4, "Board minutes"))
    assert qa.relevant("what does it do?")(_hit("e", 1, "anything"))
    assert qa.relevant("FleetPilot 的测试阶段")(_hit("f", 2, "项目测试阶段"))  # Chinese bigrams count too
