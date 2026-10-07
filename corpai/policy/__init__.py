"""Policy: one table-driven decision point, default deny (architecture §6).

Public API: `decide`, `rag_filter`, `tool_allowed`, `can_approve`, `ProposalView`, `RagFilter`,
plus `may_start` / `apps_for` (agent start rules) and `tool_tier`.
"""

from __future__ import annotations

from corpai.policy.agents import agent_spec, apps_for, load_agents, may_start
from corpai.policy.approvals import can_approve
from corpai.policy.catalogue import DOMAIN_DEPT, LABELLED_KINDS, TOOL_CATALOGUE, UNLABELLED_KINDS, tool_tier
from corpai.policy.engine import decide, rag_filter, tool_allowed
from corpai.policy.model import AgentSpec, ProposalView, RagFilter

__all__ = [
    "decide", "rag_filter", "tool_allowed", "can_approve", "ProposalView", "RagFilter",
    "may_start", "apps_for", "agent_spec", "load_agents", "AgentSpec",
    "tool_tier", "TOOL_CATALOGUE", "DOMAIN_DEPT", "LABELLED_KINDS", "UNLABELLED_KINDS",
]
