"""Embedded Postgres (pgserver) lifecycle, connections and the per-request principal transaction.

Frozen core (docs/architecture.md §4). Two login roles:
  corpai_owner  owns every table; used only by migrations, synth, ingestion, metric refresh and
                proposal executors. Owners bypass RLS, so request paths must never use it.
  corpai_app    NOSUPERUSER NOBYPASSRLS; every request path. RLS reads the session variables that
                principal_tx() sets; outside principal_tx it sees nothing (fail-closed).

Windows gotcha: pgserver's cleanup() does not stop postgres, and the port changes on every start,
so we attach through postmaster.pid and stop with `pg_ctl stop -m fast` ourselves. On macOS and Linux
pgserver listens on a Unix socket only, so the connection string carries the socket dir as `host`.
"""

from __future__ import annotations

import subprocess
import threading
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import AsyncIterator, Iterator

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from corpai.config import settings
from corpai.types import Principal

DB_NAME = "corpai"
OWNER_ROLE = "corpai_owner"
APP_ROLE = "corpai_app"

_lock = threading.Lock()
_base_uri: dict[Path, str] = {}  # data dir -> superuser URI of the running server
_app_pool = None  # psycopg_pool.AsyncConnectionPool installed by the API lifespan


def _pg_bin(name: str) -> str:
    from pgserver._commands import POSTGRES_BIN_PATH

    return str(POSTGRES_BIN_PATH / name)


def _swap(uri: str, user: str, database: str) -> str:
    # pgserver URIs: postgresql://postgres:@127.0.0.1:PORT/postgres (TCP, Windows) or
    # postgresql://postgres:@/postgres?host=<socket dir> (Unix socket, macOS/Linux). make_conninfo keeps
    # the host, port and socket dir and replaces only the user and database.
    return make_conninfo(uri, user=user, dbname=database)


def _bootstrap(super_uri: str) -> None:
    """Idempotent: roles, database, extension. Runs as the postgres superuser."""
    with psycopg.connect(super_uri, autocommit=True) as conn:
        roles = {r[0] for r in conn.execute("SELECT rolname FROM pg_roles")}
        if OWNER_ROLE not in roles:
            conn.execute(f"CREATE ROLE {OWNER_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE")
        if APP_ROLE not in roles:
            conn.execute(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOINHERIT")
        dbs = {r[0] for r in conn.execute("SELECT datname FROM pg_database")}
        if DB_NAME not in dbs:
            conn.execute(f"CREATE DATABASE {DB_NAME} OWNER {OWNER_ROLE}")
    with psycopg.connect(_swap(super_uri, "postgres", DB_NAME), autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"REVOKE ALL ON DATABASE {DB_NAME} FROM PUBLIC")
        conn.execute(f"GRANT CONNECT ON DATABASE {DB_NAME} TO {APP_ROLE}")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute(f"ALTER SCHEMA public OWNER TO {OWNER_ROLE}")


def ensure_server(data_dir: Path | None = None) -> str:
    """Start pgserver (or attach to a running one) and bootstrap it. Returns the owner DSN."""
    import pgserver

    data_dir = Path(data_dir or settings().pg_dir).resolve()
    with _lock:
        if data_dir not in _base_uri:
            data_dir.mkdir(parents=True, exist_ok=True)
            srv = pgserver.get_server(data_dir, cleanup_mode=None)  # we stop it explicitly
            uri = srv.get_uri()
            _bootstrap(uri)
            _base_uri[data_dir] = uri
        return _swap(_base_uri[data_dir], OWNER_ROLE, DB_NAME)


def stop_server(data_dir: Path | None = None) -> None:
    data_dir = Path(data_dir or settings().pg_dir).resolve()
    with _lock:
        _base_uri.pop(data_dir, None)
    if (data_dir / "postmaster.pid").exists():
        subprocess.run([_pg_bin("pg_ctl"), "-D", str(data_dir), "stop", "-m", "fast", "-w"],
                       check=False, capture_output=True, timeout=60)


def _current_base() -> str:
    ensure_server()
    return _base_uri[Path(settings().pg_dir).resolve()]


def owner_dsn() -> str:
    return _swap(_current_base(), OWNER_ROLE, DB_NAME)


def app_dsn() -> str:
    return _swap(_current_base(), APP_ROLE, DB_NAME)


def superuser_dsn() -> str:
    """Bootstrap and test teardown only."""
    return _swap(_current_base(), "postgres", DB_NAME)


def connect_owner(*, autocommit: bool = False) -> psycopg.Connection:
    """Sync owner connection (dict rows). `with connect_owner() as c:` commits on success."""
    return psycopg.connect(owner_dsn(), row_factory=dict_row, autocommit=autocommit)


def connect_app(*, autocommit: bool = False) -> psycopg.Connection:
    """Sync app-role connection without a principal: sees nothing under RLS. Tests use it to prove that."""
    return psycopg.connect(app_dsn(), row_factory=dict_row, autocommit=autocommit)


# ---- principal transactions -------------------------------------------------------------

SESSION_VARS = ("app.user_id", "app.role", "app.dept", "app.groups", "app.clearance", "app.run_id")


def session_vars(principal: Principal, run_id: str | None = None) -> list[tuple[str, str]]:
    """The (name, value) pairs RLS reads. Public so other connection holders (the per-run
    checkpoint connection) set exactly what principal_tx sets."""
    for g in principal.groups:
        if "," in g:
            raise ValueError(f"ACL group names must not contain commas: {g!r}")
    if not principal.active:
        groups, clearance = "", "0"  # deactivated users see nothing
    else:
        groups, clearance = ",".join(sorted(principal.groups)), str(int(principal.clearance))
    return [
        ("app.user_id", principal.user_id),
        ("app.role", str(principal.role)),
        ("app.dept", str(principal.dept)),
        ("app.groups", groups),
        ("app.clearance", clearance),
        ("app.run_id", run_id or ""),
    ]


_SET = "SELECT set_config(%s, %s, true)"  # is_local=true: reset when the transaction ends


def install_app_pool(pool) -> None:
    """The API lifespan creates an AsyncConnectionPool(app_dsn(), kwargs={'row_factory': dict_row})."""
    global _app_pool
    _app_pool = pool


def app_pool():
    """The installed AsyncConnectionPool, or None outside the API process."""
    return _app_pool


@asynccontextmanager
async def app_tx() -> AsyncIterator[psycopg.AsyncConnection]:
    """One transaction as corpai_app with NO principal: RLS shows nothing.

    Only for SECURITY DEFINER entry points that run before or without a principal
    (principal lookup, sessions, outage switches)."""
    if _app_pool is not None:
        async with _app_pool.connection() as conn:
            async with conn.transaction():
                yield conn
        return
    conn = await psycopg.AsyncConnection.connect(app_dsn(), row_factory=dict_row)
    async with conn:
        async with conn.transaction():
            yield conn


@asynccontextmanager
async def principal_tx(principal: Principal, *, run_id: str | None = None) -> AsyncIterator[psycopg.AsyncConnection]:
    """One transaction as corpai_app with the principal's session variables. RLS does the rest."""
    if _app_pool is not None:
        async with _app_pool.connection() as conn:
            async with conn.transaction():
                for name, value in _session_vars(principal, run_id):
                    await conn.execute(_SET, (name, value))
                yield conn
        return
    conn = await psycopg.AsyncConnection.connect(app_dsn(), row_factory=dict_row)
    async with conn:
        async with conn.transaction():
            for name, value in _session_vars(principal, run_id):
                await conn.execute(_SET, (name, value))
            yield conn


@contextmanager
def principal_tx_sync(principal: Principal, *, run_id: str | None = None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(app_dsn(), row_factory=dict_row) as conn:
        with conn.transaction():
            for name, value in _session_vars(principal, run_id):
                conn.execute(_SET, (name, value))
            yield conn


_session_vars = session_vars  # kept for callers written before it became public
