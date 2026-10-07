"""The frozen DB core: roles, RLS through principal_tx, fail-closed outside it."""

from __future__ import annotations

import pytest

from corpai.db import pg as pgmod
from corpai.types import Dept, Level, Principal, Role


def _p(user_id: str, groups: set[str], clearance: Level = Level.L3, active: bool = True) -> Principal:
    return Principal(user_id=user_id, name=user_id, email=f"{user_id}@abctech.example", dept=Dept.SALES,
                     role=Role.EMPLOYEE, title="t", manager_id=None, clearance=clearance,
                     groups=frozenset(groups), active=active)


@pytest.fixture(scope="module")
def demo_table(pg):
    with pgmod.connect_owner() as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS coretest")
        c.execute("""CREATE TABLE IF NOT EXISTS coretest.items (
                       id text PRIMARY KEY, level smallint NOT NULL, acl_groups text[] NOT NULL)""")
        c.execute("TRUNCATE coretest.items")
        c.execute("""INSERT INTO coretest.items VALUES
                       ('pub', 1, '{all}'), ('sales', 3, '{dept:sales}'), ('secret', 4, '{dept:sales}')""")
        c.execute("ALTER TABLE coretest.items ENABLE ROW LEVEL SECURITY")
        c.execute("DROP POLICY IF EXISTS items_read ON coretest.items")
        c.execute("""CREATE POLICY items_read ON coretest.items FOR SELECT TO corpai_app USING (
                       level <= coalesce(nullif(current_setting('app.clearance', true), '')::int, 0)
                       AND acl_groups && string_to_array(current_setting('app.groups', true), ','))""")
        c.execute("GRANT USAGE ON SCHEMA coretest TO corpai_app")
        c.execute("GRANT SELECT ON coretest.items TO corpai_app")
    yield
    with pgmod.connect_owner() as c:
        c.execute("DROP SCHEMA coretest CASCADE")


def _ids(conn) -> set[str]:
    return {r["id"] for r in conn.execute("SELECT id FROM coretest.items").fetchall()}


def test_app_role_sees_nothing_without_principal(demo_table):
    with pgmod.connect_app() as conn:
        assert _ids(conn) == set()


def test_principal_tx_sync_applies_groups_and_clearance(demo_table):
    with pgmod.principal_tx_sync(_p("u1", {"all", "dept:sales"}, Level.L3)) as conn:
        assert _ids(conn) == {"pub", "sales"}
    with pgmod.principal_tx_sync(_p("u2", {"all"}, Level.L4)) as conn:
        assert _ids(conn) == {"pub"}


def test_deactivated_principal_sees_nothing(demo_table):
    with pgmod.principal_tx_sync(_p("u3", {"all", "dept:sales"}, Level.L4, active=False)) as conn:
        assert _ids(conn) == set()


def test_session_vars_reset_after_transaction(demo_table):
    with pgmod.connect_app() as conn:
        with conn.transaction():
            for name, value in pgmod._session_vars(_p("u1", {"all"}), None):
                conn.execute(pgmod._SET, (name, value))
            assert _ids(conn) == {"pub"}
        assert _ids(conn) == set()


@pytest.mark.anyio
async def test_principal_tx_async(demo_table):
    async with pgmod.principal_tx(_p("u1", {"all", "dept:sales"}, Level.L4)) as conn:
        cur = await conn.execute("SELECT id FROM coretest.items")
        assert {r["id"] for r in await cur.fetchall()} == {"pub", "sales", "secret"}


def test_owner_is_not_superuser(pg):
    with pgmod.connect_owner() as c:
        row = c.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
        assert row == {"rolsuper": False, "rolbypassrls": False}


@pytest.mark.anyio
async def test_app_tx_has_no_principal(demo_table):
    async with pgmod.app_tx() as conn:
        cur = await conn.execute("SELECT id FROM coretest.items")
        assert await cur.fetchall() == []
    assert pgmod.app_pool() is None  # no API pool in tests unless installed
