"""Permission-filtered hybrid retrieval (architecture §10, design-review 二.5).

The filter is built by code from the principal (policy.rag_filter), never by a model, and pushed into
the SQL WHERE clause of both searches; RLS on rag.chunks applies underneath as a second, independent
check. Filtering before ranking means the top k can never be filled with rows the caller cannot read.
"""

from __future__ import annotations

import asyncio
from typing import Iterable

from corpai import audit
from corpai.db import pg
from corpai.embed import get_embedder, to_pgvector
from corpai.policy import RagFilter, rag_filter
from corpai.rag.model import Hit, RetrievalResult
from corpai.types import TRUST_RANK, Level, Principal, Trust

CANDIDATES = 30  # per search arm, before fusion
RRF_K = 60  # reciprocal-rank fusion constant
MAX_K = 50
MAX_QUERY_CHARS = 2000
# pgvector 0.6 has no iterative index scan: HNSW returns ef_search candidates and only then applies the
# WHERE clause and RLS. With the default of 40, a strict filter (one small collection, a narrow ACL)
# can leave far fewer than CANDIDATES rows (issues-log). 200 keeps recall up at this corpus size.
EF_SEARCH = 200

_COLS = "chunk_id, doc_id, version, title, heading, text, level, trust, collection, source_uri"
_FILTER = ("collection = ANY(%(cols)s) AND level <= %(max_level)s AND acl_groups && %(groups)s::text[] "
           "AND trust = ANY(%(trusts)s) AND status = ANY(%(statuses)s)")
# No tie-breaker after the distance: a second sort key can stop the HNSW index from providing the order.
_VEC_SQL = (f"SELECT {_COLS} FROM rag.chunks WHERE {_FILTER} "
            "ORDER BY embedding <=> %(qv)s::vector LIMIT %(n)s")
# websearch_to_tsquery ANDs every word, so a natural-language question rarely matches a whole chunk.
# Turning the ANDs into ORs lets ts_rank order chunks by how many query terms they cover. Queries with
# a negation (-word → !) keep their AND form so the exclusion still means something.
_TXT_SQL = (
    "WITH w AS (SELECT websearch_to_tsquery('english', %(qt)s)::text AS t), "
    "q AS (SELECT (CASE WHEN strpos(t, '!') > 0 THEN t ELSE replace(t, ' & ', ' | ') END)::tsquery AS q FROM w) "
    f"SELECT {_COLS}, ts_rank(c.tsv, q.q) AS rank FROM rag.chunks c, q WHERE {_FILTER} AND c.tsv @@ q.q "
    "ORDER BY rank DESC, chunk_id LIMIT %(n)s"
)


def _trusts(min_trust: Trust | None) -> list[str]:
    floor = TRUST_RANK[min_trust] if min_trust is not None else 0
    return [str(t) for t, r in TRUST_RANK.items() if r >= floor]


def _fuse(vec_rows: list[dict], txt_rows: list[dict], k: int) -> list[Hit]:
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for arm in (vec_rows, txt_rows):
        for rank, row in enumerate(arm, start=1):
            scores[row["chunk_id"]] = scores.get(row["chunk_id"], 0.0) + 1.0 / (RRF_K + rank)
            rows[row["chunk_id"]] = row
    best = sorted(scores, key=lambda cid: (-scores[cid], cid))[:k]
    return [Hit(chunk_id=cid, doc_id=rows[cid]["doc_id"], version=rows[cid]["version"], title=rows[cid]["title"],
                heading=rows[cid]["heading"], text=rows[cid]["text"], level=Level(rows[cid]["level"]),
                trust=Trust(rows[cid]["trust"]), collection=rows[cid]["collection"], score=scores[cid],
                source_uri=rows[cid]["source_uri"]) for cid in best]


async def _search(p: Principal, flt: RagFilter, cols: frozenset[str], query: str, k: int,
                  run_id: str | None) -> list[Hit]:
    embedder = get_embedder()
    # fastembed is CPU-bound; keep the event loop free
    vec = (await asyncio.to_thread(embedder.embed, [query]))[0]
    params = {
        "cols": sorted(cols), "max_level": int(flt.max_level), "groups": sorted(flt.groups),
        "trusts": _trusts(flt.min_trust),
        "statuses": ["current"] if flt.current_only else ["current", "superseded", "draft"],
        "qv": to_pgvector(vec), "qt": query, "n": CANDIDATES,
    }
    async with pg.principal_tx(p, run_id=run_id) as conn:
        await conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(EF_SEARCH),))
        vec_rows = await (await conn.execute(_VEC_SQL, params)).fetchall()
        txt_rows = await (await conn.execute(_TXT_SQL, params)).fetchall()
    return _fuse(vec_rows, txt_rows, k)


async def retrieve(p: Principal, query: str, *, purpose: str, k: int = 8,
                   collections: Iterable[str] | None = None, run_id: str | None = None,
                   agent: str | None = None) -> RetrievalResult:
    """Top-k chunks the principal may read for this purpose. Audited as `retrieval` (metadata only)."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if isinstance(collections, str):  # a bare string would iterate as characters
        raise TypeError("collections must be an iterable of collection names, not a string")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise ValueError(f"k must be between 1 and {MAX_K}")
    flt = rag_filter(p, purpose=purpose)  # ValueError for an unknown purpose
    cols = flt.collections if collections is None else flt.collections & frozenset(map(str, collections))
    q = query.strip()[:MAX_QUERY_CHARS]

    hits: list[Hit] = []
    if not p.active:
        reason, decision = "inactive", "deny"
    elif not cols or not flt.groups:
        reason, decision = "no_collections", "deny"
    elif not q:
        reason, decision = "empty_query", "allow"
    else:
        hits = await _search(p, flt, cols, q, k, run_id)
        reason, decision = "rag_filter", "allow"
    level = max((h.level for h in hits), default=Level.L1)

    # Metadata only: never the query or chunk text (they may hold anything up to L4).
    await audit.arecord(
        "retrieval", actor=p.user_id, act_chain=(f"agent:{agent}",) if agent else (), action="search",
        resource=f"rag:{purpose}", decision=decision, reason=reason, level=int(level), run_id=run_id,
        details={"purpose": purpose, "count": len(hits), "k": k, "collections": sorted(cols),
                 "hit_collections": sorted({h.collection for h in hits}),
                 "doc_ids": sorted({h.doc_id for h in hits}),
                 "min_trust": str(flt.min_trust) if flt.min_trust else None})
    return RetrievalResult(hits=hits, level=level, filter=flt, collections=cols)
