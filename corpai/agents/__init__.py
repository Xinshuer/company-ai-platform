"""Agent graphs and their runtime (architecture §12).

Public API:
    RunContext, Services, BaseState, max_level             context_schema and the state base
    llm, call_tool, retrieve, approval_gate, llm_update     helpers every node uses (hwm only goes up)
    Step                                                   one node's helpers with a shared update
    node(caps=..., gate=False)                             Rule-of-Two capabilities + node span
    published(text), PublicText                            published content for ChatRequest.public_only
    router.resolve(principal, app) / router.register_app   app → graph (corpai.agents.router)
Graphs: corpai.agents.qa.build_graph() (M1); milestone packages add theirs.
"""

from __future__ import annotations

from corpai.agents.runtime import (
    CAPS,
    BaseState,
    PublicText,
    RunContext,
    Services,
    Step,
    approval_gate,
    call_tool,
    current_hwm,
    llm,
    llm_update,
    max_level,
    merge_updates,
    node,
    node_caps,
    published,
    raise_hwm,
    retrieve,
)

__all__ = [
    "CAPS", "BaseState", "PublicText", "RunContext", "Services", "Step", "approval_gate", "call_tool",
    "current_hwm", "llm", "llm_update", "max_level", "merge_updates", "node", "node_caps", "published",
    "raise_hwm", "retrieve",
]
