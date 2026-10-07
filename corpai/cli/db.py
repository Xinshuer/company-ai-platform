"""python -m corpai db up|down|status|reset"""

from __future__ import annotations

import shutil

from corpai.config import settings
from corpai.db import pg


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
        return 0
    print("usage: python -m corpai db up|down|status|reset [--yes]")
    return 2
