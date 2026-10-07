"""directory.get_principal*/list_personas against a real pg: no cache, grants expire, deactivation."""

from __future__ import annotations

import pytest

from corpai.config import reload_settings
from corpai.db import pg as pgmod
from corpai.identity import directory
from corpai.types import Dept, Level, Role
from tests.identity.dbutil import add_grant, add_membership, add_user, owner_exec


def test_unknown_and_invalid_ids(pg):
    assert directory.get_principal_sync("nobody-here") is None
    for bad in ("", None, 42, "x" * 500):
        assert directory.get_principal_sync(bad) is None


def test_builds_principal_with_groups(pg):
    boss = add_user(dept="eng", role="manager", clearance=4)
    dev = add_user(dept="eng", clearance=3, manager_id=boss)
    add_membership(dev, "proj:P-77")
    p = directory.get_principal_sync(dev)
    assert p.user_id == dev and p.dept is Dept.ENG and p.role is Role.EMPLOYEE
    assert p.clearance is Level.L3 and p.manager_id == boss and p.active
    assert p.groups == {"all", "dept:eng", f"user:{dev}", "proj:P-77"}
    b = directory.get_principal_sync(boss)
    assert {"head:eng", f"mgr-of:{dev}"} <= b.groups


def test_grant_expiry_and_revocation_take_effect_immediately(pg):
    uid = add_user(dept="legal", clearance=4)
    gid = add_grant(uid, "grant:crm:A-0099")
    assert "grant:crm:A-0099" in directory.get_principal_sync(uid).groups
    owner_exec("UPDATE core.grants SET expires_at = now() - interval '1 second' WHERE grant_id = %s", (gid,))
    assert "grant:crm:A-0099" not in directory.get_principal_sync(uid).groups  # no cache
    gid2 = add_grant(uid, "grant:crm:A-0100")
    assert "grant:crm:A-0100" in directory.get_principal_sync(uid).groups
    owner_exec("UPDATE core.grants SET revoked_at = now() WHERE grant_id = %s", (gid2,))
    assert "grant:crm:A-0100" not in directory.get_principal_sync(uid).groups


def test_deactivation_takes_effect_immediately(pg):
    boss = add_user(dept="sales", role="manager", clearance=4)
    rep = add_user(dept="sales", clearance=3, manager_id=boss)
    add_membership(rep, "proj:P-88")
    assert directory.get_principal_sync(rep).groups
    owner_exec("UPDATE core.users SET active = false WHERE user_id = %s", (rep,))
    p = directory.get_principal_sync(rep)
    assert p is not None and p.active is False and p.groups == frozenset()
    # the manager no longer manages a deactivated report
    assert f"mgr-of:{rep}" not in directory.get_principal_sync(boss).groups
    # and RLS gives a deactivated principal nothing (clearance 0, no groups)
    with pgmod.principal_tx_sync(p) as conn:
        row = conn.execute("SELECT core.cur_clearance() AS c, core.cur_groups() AS g").fetchone()
        assert row == {"c": 0, "g": []}


def test_service_account(pg):
    uid = add_user(user_id="svc:testjob", dept="it", role="service", clearance=4)
    p = directory.get_principal_sync(uid)
    assert p.role is Role.SERVICE and p.groups == {"svc:testjob"}


@pytest.mark.anyio
async def test_async_get_principal(pg):
    uid = add_user(dept="finance", clearance=4)
    p = await directory.get_principal(uid)
    assert p is not None and "dept:finance" in p.groups
    assert await directory.get_principal("missing-user") is None
    owner_exec("UPDATE core.users SET active = false WHERE user_id = %s", (uid,))
    assert (await directory.get_principal(uid)).groups == frozenset()


def test_app_role_cannot_read_directory_tables(pg):
    with pgmod.connect_app() as conn:
        for table in ("core.users", "core.grants", "core.memberships", "core.sessions"):
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(f"SELECT * FROM {table}")
            conn.rollback()


def test_list_personas(pg):
    key = "test_persona_a3"
    uid = add_user(dept="hr", clearance=4, persona_key=key)
    gone = add_user(dept="hr", clearance=4, persona_key=key + "_gone", active=False)
    plain = add_user(dept="hr", clearance=4)
    rows = {r["user_id"]: r for r in directory.list_personas_sync()}
    assert uid in rows and gone not in rows and plain not in rows
    assert set(rows[uid]) == {"user_id", "name", "title", "dept", "role", "persona_key"}  # no email, no clearance
    assert rows[uid]["persona_key"] == key


@pytest.mark.anyio
async def test_list_personas_async(pg):
    uid = add_user(dept="eng", clearance=3, persona_key="test_persona_async")
    assert uid in {r["user_id"] for r in await directory.list_personas()}


@pytest.mark.anyio
async def test_list_personas_refused_without_demo_login(pg, monkeypatch):
    monkeypatch.setenv("CORPAI_DEMO_LOGIN", "0")
    reload_settings()
    try:
        with pytest.raises(directory.DemoLoginDisabled):
            directory.list_personas_sync()
        with pytest.raises(PermissionError):
            await directory.list_personas()
    finally:
        monkeypatch.setenv("CORPAI_DEMO_LOGIN", "1")
        reload_settings()
