"""Frozen core migrations: helper functions and the directory resolver."""

from __future__ import annotations

from corpai.db import pg as pgmod
from corpai.db.migrate import apply_migrations
from corpai.types import Dept, Level, Principal, Role


def test_migrations_idempotent(pg):
    with pgmod.connect_owner() as conn:
        assert apply_migrations(conn) == []


def test_principal_row_is_the_only_way_in(pg):
    with pgmod.connect_owner() as c:
        c.execute("""INSERT INTO core.users (user_id, name, email, dept, role, title, clearance)
                     VALUES ('u_mig1', 'Test Boss', 'boss@abctech.example', 'sales', 'manager', 'Head', 4),
                            ('u_mig2', 'Test Rep', 'rep@abctech.example', 'sales', 'employee', 'Rep', 3)
                     ON CONFLICT DO NOTHING""")
        c.execute("UPDATE core.users SET manager_id = 'u_mig1' WHERE user_id = 'u_mig2'")
        c.execute("INSERT INTO core.memberships VALUES ('u_mig2', 'proj:P-01') ON CONFLICT DO NOTHING")
        c.execute("""INSERT INTO core.grants (grant_id, user_id, grp, reason, granted_by, expires_at)
                     VALUES ('g1', 'u_mig2', 'grant:acct:A-1', 'ticket', 'u_mig1', now() + interval '1 hour'),
                            ('g2', 'u_mig2', 'grant:old', 'expired', 'u_mig1', now() - interval '1 hour')
                     ON CONFLICT DO NOTHING""")
    with pgmod.connect_app() as conn:
        rows = conn.execute("SELECT * FROM core.principal_row('u_mig2')").fetchall()
        assert len(rows) == 1
        assert rows[0]["memberships"] == ["proj:P-01"]
        assert rows[0]["grants"] == ["grant:acct:A-1"]
        boss = conn.execute("SELECT direct_reports FROM core.principal_row('u_mig1')").fetchone()
        assert boss["direct_reports"] == ["u_mig2"]
        assert conn.execute("SELECT * FROM core.principal_row('nobody')").fetchall() == []
        conn.rollback()
        try:
            conn.execute("SELECT * FROM core.users")
            raise AssertionError("app role must not read core.users directly")
        except Exception as e:  # permission denied
            assert "permission denied" in str(e)


def test_can_read_helper(pg):
    p = Principal(user_id="x", name="x", email="x@abctech.example", dept=Dept.ENG, role=Role.EMPLOYEE,
                  title="t", manager_id=None, clearance=Level.L3, groups=frozenset({"all", "dept:eng"}))
    with pgmod.principal_tx_sync(p) as conn:
        q = "SELECT core.can_read(%s::smallint, %s::text[]) AS ok"
        assert conn.execute(q, (3, ["dept:eng"])).fetchone()["ok"] is True
        assert conn.execute(q, (4, ["dept:eng"])).fetchone()["ok"] is False
        assert conn.execute(q, (1, ["dept:sales"])).fetchone()["ok"] is False
    with pgmod.connect_app() as conn:
        assert conn.execute("SELECT core.can_read(1::smallint, '{all}') AS ok").fetchone()["ok"] is False
