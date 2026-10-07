"""docs server: search and read documents through corpai.rag (architecture §10, §11).

Every human may use it; RLS and the agent's retrieval purpose decide which documents come back.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context

from corpai.mcp_servers import _rag
from corpai.mcp_servers._common import check_id, check_limit, check_text
from corpai.mcp_servers._envelope import Collector, run_tool

DOMAIN = "docs"


async def search_docs(ctx: Context, query: str, k: int = 8) -> dict:
    """Hybrid search over the documents you may read, within this assistant's document scope."""
    async def body(col: Collector) -> dict:
        q = check_text("query", query, max_len=1000)
        kk = check_limit(k, default=8, maximum=20)
        return await _rag.search(col, q, k=kk, purpose=_rag.purpose_for(col))
    return await run_tool(ctx, DOMAIN, "search_docs", body)


async def get_doc(ctx: Context, doc_id: str) -> dict:
    """The current version of one document, chunk by chunk."""
    async def body(col: Collector) -> dict:
        did = check_id("doc_id", doc_id)
        col.resource = f"doc:{did}"
        return await _rag.get_doc(col, did, purpose=_rag.purpose_for(col))
    return await run_tool(ctx, DOMAIN, "get_doc", body)


TOOLS = [search_docs, get_doc]
