"""Ingestion (owner job): label, chunk, embed and store documents with versioning (architecture §10).

Label (rule 4): level = max(producer's label, source_default(source), classify_text(title + text)).
It can only go up: a document is never stored below the level its producer gave it.

Versioning, per doc_id:
- the same text (content_hash) as a stored version → that version is kept; if its metadata, label or
  embedding pipeline changed, it is updated in place (label changes cascade to its chunks);
- new text → a new version; when it is current, the previous current version becomes superseded.

ingest_corpus loads the WHOLE corpus: a document that is no longer in it (synth re-run with --small
or another seed, a file deleted or renamed upstream) is retired in the same transaction, i.e. its
current and draft versions become superseded, so Q&A stops retrieving and citing rows that no
longer exist. Single documents loaded with ingest_document are not retired by anything else.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, NamedTuple

import psycopg

from corpai.classify import (GazetteerError, classify_text, gazetteer_size, load_gazetteer_from_db,
                             scrub_secrets, source_default)
from corpai.embed import get_embedder, to_pgvector
from corpai.rag.chunking import CHUNKER, Chunk, chunk_markdown
from corpai.rag.corpus import load_corpus
from corpai.rag.model import COLLECTIONS, PUBLIC_COLLECTIONS, STATUSES, DocIn, IngestError
from corpai.types import Level, Trust

_META_COLS = ("status", "collection", "title", "source", "source_uri", "project_id", "matter_id", "pipeline",
              "level", "dept", "owner_id", "acl_groups", "trust", "subject_ids")


def doc_level(doc: DocIn) -> Level:
    """Rule 4. classify_text never raises; on an internal error it returns L4 (fail closed)."""
    public = doc.collection in PUBLIC_COLLECTIONS and doc.label.trust == Trust.REVIEWED
    found = classify_text(f"{doc.title}\n\n{doc.text}", public=public).level
    return max(Level(doc.label.level), source_default(doc.source), found)


def _validate(doc: DocIn) -> None:
    if doc.collection not in COLLECTIONS:
        raise IngestError(f"{doc.doc_id}: unknown collection {doc.collection!r}")
    if doc.status not in STATUSES:
        raise IngestError(f"{doc.doc_id}: unknown status {doc.status!r}")
    if not doc.label.acl_groups:
        raise IngestError(f"{doc.doc_id}: empty acl_groups")
    if doc.version is not None and doc.version < 1:
        raise IngestError(f"{doc.doc_id}: version must be >= 1")
    if not isinstance(doc.text, str) or not isinstance(doc.title, str):
        raise IngestError(f"{doc.doc_id}: title and text must be strings")


def _meta(doc: DocIn, level: Level, pipeline: str) -> dict[str, Any]:
    lab = doc.label
    return {
        "status": doc.status, "collection": doc.collection, "title": doc.title, "source": doc.source,
        "source_uri": doc.source_uri or "", "project_id": doc.project_id, "matter_id": doc.matter_id,
        "pipeline": pipeline, "level": int(level), "dept": str(lab.dept) if lab.dept else None,
        "owner_id": lab.owner_id, "acl_groups": sorted(lab.acl_groups), "trust": str(Trust(lab.trust)),
        "subject_ids": sorted(lab.subject_ids),
    }


def _same(row: dict[str, Any], meta: dict[str, Any]) -> bool:
    return all((sorted(row[c]) if c in ("acl_groups", "subject_ids") else row[c]) == meta[c] for c in _META_COLS)


def _supersede_others(conn: psycopg.Connection, doc_id: str, keep_version: int) -> None:
    for table in ("rag.documents", "rag.chunks"):
        conn.execute(f"UPDATE {table} SET status = 'superseded' "  # table is one of our two constants
                     "WHERE doc_id = %s AND version <> %s AND status = 'current'", (doc_id, keep_version))


def _write_chunks(conn: psycopg.Connection, doc: DocIn, version: int, meta: dict[str, Any], text: str) -> int:
    # an empty body still gets one chunk, so the document can be found by its title
    chunks = chunk_markdown(text) or [Chunk("", doc.title)]
    vectors = get_embedder().embed([f"{doc.title}\n{c.heading}\n{c.text}" for c in chunks])
    rows = []
    for i, (c, vec) in enumerate(zip(chunks, vectors)):
        rows.append((f"{doc.doc_id}@v{version}#{i}", doc.doc_id, version, i, doc.title, c.heading, c.text,
                     meta["source_uri"], meta["collection"], meta["status"], meta["project_id"],
                     meta["matter_id"], to_pgvector(vec), meta["level"], meta["dept"], meta["owner_id"],
                     meta["acl_groups"], meta["trust"], meta["subject_ids"]))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO rag.chunks (chunk_id, doc_id, version, ord, title, heading, text, source_uri, collection, "
            "status, project_id, matter_id, embedding, level, dept, owner_id, acl_groups, trust, subject_ids) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s)", rows)
    return len(rows)


class _Outcome(NamedTuple):
    key: str  # "<doc_id>@v<version>"
    action: str  # inserted | updated | unchanged
    chunks: int  # chunks written
    level: Level


def _ingest(conn: psycopg.Connection, doc: DocIn) -> _Outcome:
    _validate(doc)
    if gazetteer_size() == 0:
        # Without the gazetteer, people's names classify as L1 and the document would be under-labelled.
        raise IngestError("the person-name gazetteer is not loaded; call corpai.classify.load_gazetteer_from_db() "
                          "first (ingest_corpus does)")
    level = doc_level(doc)
    pipeline = f"{get_embedder().name}|{CHUNKER}"
    meta = _meta(doc, level, pipeline)
    content_hash = hashlib.sha256(doc.text.encode("utf-8")).hexdigest()
    # Secrets never reach the index; the level above was computed on the original text, so a document
    # that contained one stays L4 even after scrubbing.
    text, _ = scrub_secrets(doc.text)
    with conn.transaction():
        # Serialise concurrent ingestion of the same doc_id (version numbers, the one-current invariant).
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"rag.ingest:{doc.doc_id}",))
        rows = conn.execute(
            "SELECT version, content_hash, " + ", ".join(_META_COLS) + " FROM rag.documents WHERE doc_id = %s "
            "ORDER BY version", (doc.doc_id,)).fetchall()
        match = next((r for r in rows if r["content_hash"] == content_hash), None)
        if match is not None:
            version = match["version"]
            key = f"{doc.doc_id}@v{version}"
            if _same(match, meta):
                return _Outcome(key, "unchanged", 0, level)
            if doc.status == "current":
                _supersede_others(conn, doc.doc_id, version)
            conn.execute("DELETE FROM rag.chunks WHERE doc_id = %s AND version = %s", (doc.doc_id, version))
            conn.execute(
                "UPDATE rag.documents SET " + ", ".join(f"{c} = %({c})s" for c in _META_COLS) + ", text = %(text)s "
                "WHERE doc_id = %(doc_id)s AND version = %(version)s",
                {**meta, "text": text, "doc_id": doc.doc_id, "version": version})
            return _Outcome(key, "updated", _write_chunks(conn, doc, version, meta, text), level)
        taken = {r["version"] for r in rows}
        version = doc.version if doc.version is not None and doc.version not in taken else max(taken, default=0) + 1
        if doc.status == "current":
            _supersede_others(conn, doc.doc_id, version)
        conn.execute(
            "INSERT INTO rag.documents (doc_id, version, content_hash, text, " + ", ".join(_META_COLS) + ") "
            "VALUES (%(doc_id)s, %(version)s, %(content_hash)s, %(text)s, "
            + ", ".join(f"%({c})s" for c in _META_COLS) + ")",
            {**meta, "doc_id": doc.doc_id, "version": version, "content_hash": content_hash, "text": text})
        return _Outcome(f"{doc.doc_id}@v{version}", "inserted", _write_chunks(conn, doc, version, meta, text), level)


def ingest_document(conn_owner: psycopg.Connection, doc: DocIn) -> str:
    """Store one document version. Returns its version key "<doc_id>@v<version>". Owner connection only."""
    return _ingest(conn_owner, doc).key


def _retire_missing(conn: psycopg.Connection, present: set[str]) -> int:
    """Supersede every current/draft document (and its chunks) whose doc_id is not in the corpus."""
    keep = sorted(present)
    gone = conn.execute("UPDATE rag.documents SET status = 'superseded' WHERE status IN ('current', 'draft') "
                        "AND NOT (doc_id = ANY(%s)) RETURNING doc_id", (keep,)).fetchall()
    conn.execute("UPDATE rag.chunks SET status = 'superseded' WHERE status IN ('current', 'draft') "
                 "AND NOT (doc_id = ANY(%s))", (keep,))
    return len({r["doc_id"] for r in gone})


def ingest_corpus(corpus_dir: Path | str | None = None) -> int:
    """Load the synth corpus into RAG in one transaction. Returns the number of new versions inserted
    (0 when re-run on an unchanged corpus). Audited as owner_job 'ingest'."""
    from corpai.audit import record_owner_job
    from corpai.config import settings
    from corpai.db import pg

    corpus_dir = Path(corpus_dir) if corpus_dir is not None else settings().corpus_dir
    docs = load_corpus(corpus_dir)  # parse everything before touching the database
    try:
        names = load_gazetteer_from_db()
    except GazetteerError as e:
        raise IngestError(f"cannot load the person-name gazetteer ({e}); people's names would be labelled L1, "
                          "so ingestion stops. Run `python -m corpai synth` and retry.") from None
    if names == 0:
        raise IngestError("the person-name gazetteer is empty; run `python -m corpai synth` first")
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "retired": 0}
    chunks = 0
    levels = {f"L{i}": 0 for i in range(1, 5)}
    with pg.connect_owner() as conn:
        with conn.transaction():
            for doc in docs:
                out = _ingest(conn, doc)
                counts[out.action] += 1
                chunks += out.chunks
                levels[f"L{int(out.level)}"] += 1
            counts["retired"] = _retire_missing(conn, {d.doc_id for d in docs})
    # Counts only: no titles or text in audit details.
    record_owner_job("ingest", {"files": len(docs), **counts, "chunks_written": chunks, "levels": levels,
                                "gazetteer_names": names, "pipeline": f"{get_embedder().name}|{CHUNKER}"})
    return counts["inserted"]
