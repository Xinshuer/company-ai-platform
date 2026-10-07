"""python -m corpai ingest [--corpus-dir DIR]

Starts (or attaches to) the embedded Postgres, applies migrations and loads the synth corpus into RAG.
Run `python -m corpai synth` first: ingestion needs the corpus files and the person-name gazetteer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpai ingest", description="Load the document corpus into RAG.")
    ap.add_argument("--corpus-dir", type=Path, default=None, help="default: <CORPAI_DATA_DIR>/corpus")
    args = ap.parse_args(argv)

    from corpai.config import settings
    from corpai.db import pg
    from corpai.db.migrate import apply_migrations
    from corpai.rag import IngestError, ingest_corpus

    pg.ensure_server()
    with pg.connect_owner() as conn:
        apply_migrations(conn)
    corpus_dir = args.corpus_dir or settings().corpus_dir
    try:
        inserted = ingest_corpus(corpus_dir)
    except IngestError as e:
        print(f"ingest failed: {e}", file=sys.stderr)
        return 1
    with pg.connect_owner() as conn:
        row = conn.execute("SELECT count(*) FILTER (WHERE status = 'current') AS current, count(*) AS versions "
                           "FROM rag.documents").fetchone()
        chunks = conn.execute("SELECT count(*) AS n FROM rag.chunks").fetchone()["n"]
    print(f"ingested {corpus_dir}: {inserted} new version(s); rag now holds {row['current']} current document(s), "
          f"{row['versions']} version(s), {chunks} chunk(s); embedder={settings().embed_mode}")
    return 0
