"""Permission-aware RAG (architecture §10).

    ingest_document(conn_owner, doc: DocIn) -> str      label (rule 4), chunk, embed, version; owner only
    ingest_corpus(corpus_dir) -> int                    the synth corpus; loads the gazetteer first; audited
    async retrieve(p, query, *, purpose, k=8, collections=None) -> RetrievalResult
                                                        rag_filter in the WHERE clause + RLS underneath
"""

from __future__ import annotations

from corpai.rag.chunking import Chunk, chunk_markdown
from corpai.rag.corpus import load_corpus, split_front_matter
from corpai.rag.ingest import doc_level, ingest_corpus, ingest_document
from corpai.rag.model import COLLECTIONS, PUBLIC_COLLECTIONS, DocIn, Hit, IngestError, RetrievalResult
from corpai.rag.retrieve import retrieve

__all__ = [
    "COLLECTIONS", "PUBLIC_COLLECTIONS", "Chunk", "DocIn", "Hit", "IngestError", "RetrievalResult",
    "chunk_markdown", "doc_level", "ingest_corpus", "ingest_document", "load_corpus", "retrieve",
    "split_front_matter",
]
