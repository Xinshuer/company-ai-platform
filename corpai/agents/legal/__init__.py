"""M2 legal_review app: contract review, drafting, expiry and sending (architecture §16 M2).

The router (corpai/agents/router.py, MILESTONE_APPS) imports this package and uses
build_graph() / initial_state(). The graph and its Rule-of-Two layout are described in
corpai/agents/legal/graph.py; the input format in corpai/agents/legal/_input.py; uploaded files in
corpai/agents/legal/uploads.py (used by corpai/api/routes_legal.py).
"""

from __future__ import annotations

from corpai.agents.legal.graph import AGENT, APP, LegalState, build_graph, initial_state

__all__ = ["AGENT", "APP", "LegalState", "build_graph", "initial_state"]
