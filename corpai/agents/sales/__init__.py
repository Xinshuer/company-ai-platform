"""M4 sales graphs: sales_profile (app) and its isolated web sub-agent sales_web.

    build_sales_profile_graph(), sales_profile_initial_state(text)   what the router uses
    build_sales_web_graph(), run_web_research(ctx, company)          the sub-agent (agent sales_web)
    FOLLOWUP_RE                                                      "/followup [CT-xxxxx]" input
"""

from __future__ import annotations

from corpai.agents.sales.graph import FOLLOWUP_RE, PROMPT_FIELDS, SalesState
from corpai.agents.sales.graph import build_graph as build_sales_profile_graph
from corpai.agents.sales.graph import initial_state as sales_profile_initial_state
from corpai.agents.sales.web import build_sales_web_graph, run_web_research

__all__ = ["FOLLOWUP_RE", "PROMPT_FIELDS", "SalesState", "build_sales_profile_graph", "build_sales_web_graph",
           "run_web_research", "sales_profile_initial_state"]
