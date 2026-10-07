"""python -m corpai db up|down|status|reset"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from corpai.config import settings
from corpai.db import pg


def archive_anchors(data_dir: Path) -> Path | None:
    """Move the audit anchor file aside after a reset; returns the archive path, or None.

    The anchors pin the head of the audit chain that the reset just deleted. Left in place, every
    later verify_chain would report the new chain as tampered (first_bad_seq 2) although its links
    are intact. The old file is kept, renamed, as the record of what was anchored before the reset;
    the new chain starts a new file at its first owner job.
    """
    from corpai.audit.anchor import ANCHOR_FILE

    src = data_dir / ANCHOR_FILE
    if not src.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dst = src.with_name(f"{src.stem}.before-reset-{stamp}{src.suffix}")
    src.rename(dst)
    return dst


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "up":
        pg.ensure_server()
        from corpai.db.migrate import apply_migrations

        with pg.connect_owner() as conn:
            applied = apply_migrations(conn)
        print(f"postgres up at {settings().pg_dir}; applied {len(applied)} new migration(s)")
        return 0
    if cmd == "down":
        pg.stop_server()
        print("postgres stopped")
        return 0
    if cmd == "status":
        pid = settings().pg_dir / "postmaster.pid"
        print("running" if pid.exists() else "stopped", settings().pg_dir)
        return 0
    if cmd == "reset":
        if "--yes" not in argv:
            print("this deletes the database in", settings().pg_dir, "- rerun with --yes")
            return 1
        pg.stop_server()
        shutil.rmtree(settings().pg_dir, ignore_errors=True)
        print("deleted", settings().pg_dir)
        archived = archive_anchors(settings().data_dir)
        if archived is not None:
            print("audit anchors of the deleted chain moved to", archived)
        return 0
    print("usage: python -m corpai db up|down|status|reset [--yes]")
    return 2
