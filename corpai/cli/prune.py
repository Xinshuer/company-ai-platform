"""python -m corpai prune [--days N]

Deletes LangGraph checkpoints (and their ops.threads rows) of threads idle for more than N days.
N defaults to CORPAI_CHECKPOINT_TTL_DAYS or 30. An owner job, so it is audited (owner_job prune).
"""

from __future__ import annotations

import argparse
import os
import sys

DEFAULT_TTL_DAYS = 30


def ttl_days() -> int:
    raw = os.environ.get("CORPAI_CHECKPOINT_TTL_DAYS", "")
    try:
        n = int(raw) if raw else DEFAULT_TTL_DAYS
    except ValueError:
        n = DEFAULT_TTL_DAYS
    return n if n >= 1 else DEFAULT_TTL_DAYS


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpai prune", description="Delete old conversation checkpoints.")
    ap.add_argument("--days", type=int, default=None, help="checkpoint TTL in days (default: checkpoint_ttl_days)")
    args = ap.parse_args(argv)
    days = args.days if args.days is not None else ttl_days()
    if days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2

    from corpai import audit
    from corpai.agents._checkpoint import prune_sync
    from corpai.db import pg
    from corpai.db.migrate import apply_migrations

    pg.ensure_server()
    with pg.connect_owner() as conn:
        apply_migrations(conn)
    counts = prune_sync(days)
    audit.record_owner_job("prune", {"ttl_days": days, **counts})
    print(f"pruned {counts['pruned_threads']} thread(s) idle for more than {days} day(s): "
          f"{counts['checkpoints']} checkpoint(s), {counts['blobs']} blob(s), {counts['writes']} write(s)")
    return 0
