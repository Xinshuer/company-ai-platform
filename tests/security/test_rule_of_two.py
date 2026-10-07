"""Rule of Two (architecture §12, design-review 二.3): no path through any graph may hold untrusted
input, sensitive data and external action at once unless a person approves in between.

The walker is checked on synthetic graphs first (so a broken walker cannot pass the real check),
then run over every graph the router can build today.
"""

from __future__ import annotations

import pytest
from langgraph.graph import END, START, StateGraph

from corpai.agents import BaseState, RunContext, node
from corpai.agents import router
from corpai.agents._rule_of_two import graph_caps, undeclared_nodes, violations

pytestmark = pytest.mark.security

U, S, X = "untrusted_input", "sensitive_data", "external_action"


def _n(*caps, gate=False):
    @node(caps=set(caps), gate=gate)
    async def fn(state, runtime):
        return {}
    return fn


def _graph(nodes: dict, edges: list[tuple[str, str]], branches: dict | None = None) -> StateGraph:
    g = StateGraph(BaseState, context_schema=RunContext)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    for a, b in edges:
        g.add_edge(a, b)
    for src, (fn, path_map) in (branches or {}).items():
        g.add_conditional_edges(src, fn, path_map)
    return g


def test_one_node_with_all_three_fails():
    g = _graph({"a": _n(U, S, X)}, [(START, "a"), ("a", END)])
    assert [v.path for v in violations(g)] == [("__start__", "a")]


def test_caps_accumulate_along_a_path():
    g = _graph({"web": _n(U), "crm": _n(S), "send": _n(X)},
               [(START, "web"), ("web", "crm"), ("crm", "send"), ("send", END)])
    v = violations(g)
    assert v and v[0].path[-1] == "send" and v[0].caps == {U, S, X}


def test_an_approval_gate_in_between_makes_it_safe():
    g = _graph({"web": _n(U), "crm": _n(S), "gate": _n(gate=True), "send": _n(X)},
               [(START, "web"), ("web", "crm"), ("crm", "gate"), ("gate", "send"), ("send", END)])
    assert violations(g) == []


def test_a_branch_around_the_gate_is_caught():
    def pick(state):
        return "gate"

    g = _graph({"web": _n(U), "crm": _n(S), "gate": _n(gate=True), "send": _n(X)},
               [(START, "web"), ("web", "crm"), ("gate", "send"), ("send", END)],
               branches={"crm": (pick, {"gate": "gate", "skip": "send"})})
    assert any(v.path[-1] == "send" for v in violations(g))


def test_conditional_edges_without_a_path_map_can_go_anywhere():
    def pick(state) -> str:
        return "a"

    g = _graph({"a": _n(U, S), "b": _n(X)}, [(START, "a"), ("b", END)], branches={"a": (pick, None)})
    assert violations(g)


def test_undeclared_nodes_count_as_holding_everything():
    async def plain(state):
        return {}

    g = _graph({"plain": plain}, [(START, "plain"), ("plain", END)])
    assert undeclared_nodes(g) == ["plain"]
    assert violations(g)


def test_two_caps_are_fine():
    g = _graph({"web": _n(U), "draft": _n(U, X)}, [(START, "web"), ("web", "draft"), ("draft", END)])
    assert violations(g) == []


def test_subgraph_nodes_carry_their_inner_caps():
    inner = _graph({"fetch": _n(U)}, [(START, "fetch"), ("fetch", END)]).compile()
    g = _graph({"web": inner, "crm": _n(S), "send": _n(X)},
               [(START, "web"), ("web", "crm"), ("crm", "send"), ("send", END)])
    assert graph_caps(g)["web"] == ({U}, False)
    assert violations(g)


BUILT = router.buildable_apps()


def test_qa_is_among_the_checked_graphs():
    assert "qa" in BUILT


@pytest.mark.parametrize("app", sorted(BUILT))
def test_every_built_graph_keeps_the_rule_of_two(app):
    g = BUILT[app]()
    assert undeclared_nodes(g) == [], f"{app}: declare caps with @node(caps=...)"
    assert [str(v) for v in violations(g)] == []


def test_qa_holds_no_external_action():
    caps = graph_caps(router.buildable_apps()["qa"]())
    assert all(X not in c for c, _gate in caps.values())


# ---- gate nodes (review fix) -----------------------------------------------------------------------

def _raw_gate(*caps):
    """A gate node that bypasses @node's check, to prove the walker does not trust the flag."""
    async def fn(state, runtime):
        return {}
    fn.__corpai_caps__ = frozenset(caps)
    fn.__corpai_gate__ = True
    return fn


def test_a_gate_node_may_not_declare_capabilities():
    with pytest.raises(ValueError, match="gate"):
        _n(S, X, gate=True)


def test_a_gate_node_holding_all_three_itself_is_caught():
    g = _graph({"a": _raw_gate(U, S, X)}, [(START, "a"), ("a", END)])
    assert [v.path for v in violations(g)] == [("__start__", "a")]


def test_what_was_held_before_a_gate_counts_at_the_gate():
    g = _graph({"web": _n(U), "gate": _raw_gate(S, X), "after": _n()},
               [(START, "web"), ("web", "gate"), ("gate", "after"), ("after", END)])
    assert any(v.path[-1] == "gate" for v in violations(g))


def test_after_a_clean_gate_the_union_starts_again():
    g = _graph({"web": _n(U), "crm": _n(S), "gate": _n(gate=True), "send": _n(S, X)},
               [(START, "web"), ("web", "crm"), ("crm", "gate"), ("gate", "send"), ("send", END)])
    assert violations(g) == []
