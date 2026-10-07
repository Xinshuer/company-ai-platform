"""Document access for the docs and code servers, through corpai.rag (stream B1).

corpai.rag is imported lazily so these servers start (and every other tool works) before B1 is
installed; until then the document tools answer ok=false, error "rag_unavailable".
retrieve() applies policy.rag_filter and runs under RLS, so its hits are already the caller's.
"""

from __future__ import annotations

from typing import Any, Iterable

import psycopg

from corpai.mcp_servers._common import fetch_all, fetch_one, tx
from corpai.mcp_servers._envelope import NOT_VISIBLE, Collector
from corpai.policy import agent_spec, rag_filter
from corpai.types import TRUST_RANK, Trust

PUBLISHED_COLLECTIONS = frozenset({"public", "legal_regs"})


def _rag():
    try:
        from corpai import rag
    except ImportError:
        return None
    return rag if hasattr(rag, "retrieve") else None


def purpose_for(col: Collector, default: str = "qa") -> str:
    """The retrieval purpose of the calling agent (config/agents.yaml), never chosen by the model."""
    spec = agent_spec(col.auth.agent)
    return (spec.purpose if spec and spec.purpose else default)


def _field(hit: Any, name: str, default: Any = None) -> Any:
    return getattr(hit, name, default) if not isinstance(hit, dict) else hit.get(name, default)


async def search(col: Collector, query: str, *, k: int, purpose: str,
                 collections: Iterable[str] | None = None) -> dict:
    rag = _rag()
    if rag is None:
        return col.error("rag_unavailable", "document search is not available yet")
    # run_id and agent tie the retrieval audit row to the run and the calling agent (act_chain)
    result = await rag.retrieve(col.auth.principal, query, purpose=purpose, k=k,
                                collections=list(collections) if collections is not None else None,
                                run_id=col.auth.run_id or None, agent=col.auth.agent)
    hits = []
    for h in result.hits:
        level = int(_field(h, "level", 4))
        collection = _field(h, "collection")
        try:
            trust = Trust(_field(h, "trust", "internal"))
        except ValueError:
            trust = Trust.UNTRUSTED
        col.add_level(level, source=f"rag.chunk:{_field(h, 'chunk_id')}", trust=trust,
                      public=level == 1 and collection in PUBLISHED_COLLECTIONS)
        hits.append({"doc_id": _field(h, "doc_id"), "chunk_id": _field(h, "chunk_id"), "title": _field(h, "title"),
                     "text": _field(h, "text"), "collection": collection, "level": level, "trust": str(trust),
                     "score": _field(h, "score"), "source_uri": _field(h, "source_uri")})
    if not hits:
        col.add_level(int(getattr(result, "level", 1) or 1), public=True)
    return col.envelope({"hits": hits, "count": len(hits)})


async def get_doc(col: Collector, doc_id: str, *, purpose: str) -> dict:
    if _rag() is None:
        return col.error("rag_unavailable", "documents are not available yet")
    flt = rag_filter(col.auth.principal, purpose=purpose)
    try:
        async with tx(col) as conn:
            doc = await fetch_one(conn, "SELECT * FROM rag.documents WHERE doc_id = %s AND status = 'current'"
                                        " ORDER BY version DESC LIMIT 1", (doc_id,))
            chunks = [] if doc is None else await fetch_all(
                conn, "SELECT ord, heading, text FROM rag.chunks WHERE doc_id = %s AND version = %s ORDER BY ord",
                (doc_id, doc["version"]))
    except psycopg.errors.UndefinedTable:
        return col.error("rag_unavailable", "documents are not available yet")
    if doc is None:
        col.deny(f"doc:{doc_id}", NOT_VISIBLE)
        return col.error("not_found_or_denied", "no such document, or you have no permission to see it")
    # The agent's purpose limits documents the same way it limits search (rag_filter), on top of RLS.
    trust_ok = flt.min_trust is None or TRUST_RANK[Trust(doc.get("trust") or "internal")] >= TRUST_RANK[flt.min_trust]
    if doc.get("collection") not in flt.collections or int(doc["level"]) > int(flt.max_level) or not trust_ok:
        col.deny(f"doc:{doc_id}", "outside the document scope of this assistant")
        return col.error("not_found_or_denied", "this document is outside the scope of this assistant")
    meta = col.row("doc", doc_id, doc, ["doc_id", "version", "title", "collection", "source_uri", "status"])
    if meta is None:
        return col.error("not_found_or_denied", "no such document, or you have no permission to see it")
    col.public = col.public and doc.get("collection") in PUBLISHED_COLLECTIONS
    meta["chunks"] = [{"ord": c["ord"], "heading": c["heading"], "text": c["text"]} for c in chunks]
    return col.envelope({"doc": meta})
