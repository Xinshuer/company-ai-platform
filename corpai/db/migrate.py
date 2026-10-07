"""Apply migrations/NNN_<stream>_<name>.sql in order, once each, as corpai_owner.

An applied file whose content changed is an error: on a dev database run
`python -m corpai db reset --yes`; tests always start from an empty database.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from corpai.config import ROOT

MIGRATIONS_DIR = ROOT / "migrations"


class MigrationError(RuntimeError):
    pass


def _files() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    numbers = [f.name[:3] for f in files]
    dupes = {n for n in numbers if numbers.count(n) > 1}
    if dupes:
        raise MigrationError(f"duplicate migration numbers: {sorted(dupes)}")
    return files


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    """Returns the names applied in this call. Each file runs in its own transaction."""
    with conn.transaction():
        conn.execute("CREATE SCHEMA IF NOT EXISTS core")
        conn.execute("""CREATE TABLE IF NOT EXISTS core.schema_migrations (
                          name text PRIMARY KEY, sha256 text NOT NULL,
                          applied_at timestamptz NOT NULL DEFAULT now())""")
    done = {r["name"] if isinstance(r, dict) else r[0]: (r["sha256"] if isinstance(r, dict) else r[1])
            for r in conn.execute("SELECT name, sha256 FROM core.schema_migrations").fetchall()}
    applied = []
    for f in _files():
        sql = f.read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if f.name in done:
            if done[f.name] != digest:
                raise MigrationError(f"{f.name} changed after it was applied; reset the dev database "
                                     "(python -m corpai db reset --yes) or add a new migration")
            continue
        with conn.transaction():
            try:
                conn.execute(sql)  # no parameters → simple query protocol, multiple statements allowed
            except psycopg.Error as e:
                raise MigrationError(f"{f.name}: {e}") from e
            conn.execute("INSERT INTO core.schema_migrations (name, sha256) VALUES (%s, %s)", (f.name, digest))
        applied.append(f.name)
    return applied
