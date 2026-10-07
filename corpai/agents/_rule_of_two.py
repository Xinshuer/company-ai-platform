"""Rule of Two over a LangGraph graph (architecture §12, design-review 二.3).

An agent may hold at most two of: untrusted input, sensitive data, the ability to act externally.
Each node declares its capabilities with @node(caps=...). Holding a capability is sticky along a
path (content that entered earlier is still in the state), so a path is unsafe when the union of
capabilities since the last approval gate covers all three. A person deciding at a gate resets the
union for the nodes after the gate: what happens after it was approved with the full parameters in
view. The gate node itself is checked with everything held before it, and @node refuses a gate
that declares capabilities (it only waits; the runtime helpers refuse to run inside it).

Conservative on purpose: an undeclared node counts as holding every capability, a conditional edge
without a path map can go to any node, and a subgraph node holds the union of its nodes' caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from corpai.agents.runtime import CAPS, node_caps

START = "__start__"
END = "__end__"


@dataclass(frozen=True)
class Violation:
    path: tuple[str, ...]
    caps: frozenset[str]

    def __str__(self) -> str:
        return " -> ".join(self.path) + f" holds {sorted(self.caps)} without an approval gate"


def _builder(graph: Any) -> Any:
    """A StateGraph builder from a StateGraph or a compiled graph."""
    if hasattr(graph, "nodes") and hasattr(graph, "branches") and hasattr(graph, "edges"):
        return graph
    builder = getattr(graph, "builder", None)
    if builder is not None and hasattr(builder, "branches"):
        return builder
    raise TypeError("not a LangGraph StateGraph or compiled graph")


def _node_info(spec: Any) -> tuple[frozenset[str], bool]:
    runnable = getattr(spec, "runnable", spec)
    declared = node_caps(runnable)
    if declared is not None:
        return declared
    inner = getattr(runnable, "builder", None)
    if inner is not None and hasattr(inner, "branches"):
        caps: frozenset[str] = frozenset()
        for sub in inner.nodes.values():
            caps |= _node_info(sub)[0]
        return caps, False  # a gate inside a subgraph does not clear what the parent holds
    return CAPS, False  # undeclared: assume the worst


def graph_caps(graph: Any) -> dict[str, tuple[frozenset[str], bool]]:
    """node name -> (caps, is_gate)."""
    b = _builder(graph)
    return {name: _node_info(spec) for name, spec in b.nodes.items()}


def _successors(b: Any) -> dict[str, set[str]]:
    succ: dict[str, set[str]] = {n: set() for n in (*b.nodes, START)}
    for src, dst in b.edges:
        succ.setdefault(src, set()).add(dst)
    for starts, dst in getattr(b, "waiting_edges", set()):
        for src in starts:
            succ.setdefault(src, set()).add(dst)
    for src, branches in b.branches.items():
        for branch in branches.values():
            ends = getattr(branch, "ends", None)
            targets = set(ends.values()) if ends else set(b.nodes) | {END}
            succ.setdefault(src, set()).update(targets)
    for name, spec in b.nodes.items():
        ends = getattr(spec, "ends", None)
        if ends:  # Command(goto=...) destinations
            succ.setdefault(name, set()).update(ends.values() if isinstance(ends, dict) else ends)
    return succ


def violations(graph: Any) -> list[Violation]:
    """Every (shortest found) path from START that accumulates all three caps between gates."""
    b = _builder(graph)
    info = graph_caps(b)
    succ = _successors(b)
    found: list[Violation] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    stack: list[tuple[str, frozenset[str], tuple[str, ...]]] = [(START, frozenset(), (START,))]
    while stack:
        at, held, path = stack.pop()
        for nxt in sorted(succ.get(at, ())):
            if nxt == END:
                continue
            caps, gate = info.get(nxt, (CAPS, False))
            # A gate node is checked like any other first: what it holds itself, and what was held
            # before it, counts until the person has decided. Only the nodes after it start clean.
            now = held | caps
            if now >= CAPS:
                found.append(Violation(path + (nxt,), now))
                continue
            if gate:
                now = frozenset()
            key = (nxt, now)
            if key in seen:
                continue
            seen.add(key)
            stack.append((nxt, now, path + (nxt,)))
    # subgraphs must hold the rule on their own as well
    for spec in b.nodes.values():
        inner = getattr(getattr(spec, "runnable", None), "builder", None)
        if inner is not None and hasattr(inner, "branches"):
            found.extend(violations(inner))
    return found


def undeclared_nodes(graph: Any) -> list[str]:
    b = _builder(graph)
    out = []
    for name, spec in b.nodes.items():
        runnable = getattr(spec, "runnable", spec)
        if node_caps(runnable) is None and not hasattr(getattr(runnable, "builder", None), "branches"):
            out.append(name)
    return out
