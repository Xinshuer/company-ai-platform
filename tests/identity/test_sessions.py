"""Session tokens: signature + 8 h expiry + server-side revocation (core.sessions)."""

from __future__ import annotations

import time

import jwt
import pytest

from corpai.db import pg as pgmod
from corpai.identity import keys, tokens
from corpai.identity.tokens import BadSignature, SessionRevoked, TokenError, TokenExpired
from corpai.types import Dept, Level, Principal, Role
from tests.identity.dbutil import add_user, owner_exec


def test_issue_verify_revoke(pg):
    uid = add_user()
    token, sid = tokens.issue_session(uid)
    c = tokens.verify_session(token)
    assert (c.sub, c.sid) == (uid, sid) and c.exp - c.iat == 8 * 3600
    assert set(jwt.decode(token, options={"verify_signature": False})) == {"sub", "sid", "iat", "exp"}
    assert tokens.revoke_session(sid, uid) is True
    with pytest.raises(SessionRevoked) as e:
        tokens.verify_session(token)
    assert token not in str(e.value) and sid not in str(e.value)
    assert tokens.revoke_session(sid, uid) is False  # already revoked


def test_logout_revokes_only_the_callers_own_session(pg):
    a, b = add_user(), add_user()
    token_a, sid_a = tokens.issue_session(a)
    assert tokens.revoke_session(sid_a, b) is False  # someone else's user id: nothing happens
    assert tokens.verify_session(token_a).sid == sid_a
    with pgmod.connect_app() as conn:  # the old one-argument form is gone
        with pytest.raises(Exception, match="does not exist"):
            conn.execute("SELECT core.session_revoke(%s)", (sid_a,))


def test_deactivated_user_session_dies(pg):
    uid = add_user()
    token, _ = tokens.issue_session(uid)
    owner_exec("UPDATE core.users SET active = false WHERE user_id = %s", (uid,))
    with pytest.raises(SessionRevoked):
        tokens.verify_session(token)


def test_server_side_expiry(pg):
    uid = add_user()
    token, sid = tokens.issue_session(uid)
    owner_exec("UPDATE core.sessions SET expires_at = now() - interval '1 second' WHERE sid = %s", (sid,))
    with pytest.raises(SessionRevoked):
        tokens.verify_session(token)


def test_cannot_open_for_unknown_inactive_or_service(pg):
    inactive = add_user(active=False)
    svc = add_user(user_id="svc:sessiontest", dept="it", role="service", clearance=2)
    for uid in ("no-such-user", inactive, svc):
        with pytest.raises(TokenError):
            tokens.issue_session(uid)


def test_expired_jwt(pg):
    uid = add_user()
    _, sid = tokens.issue_session(uid)
    now = int(time.time())
    old = jwt.encode({"sub": uid, "sid": sid, "iat": now - 9 * 3600, "exp": now - 3600},
                     keys.load_key(keys.SESSION), algorithm="HS256")
    with pytest.raises(TokenExpired):
        tokens.verify_session(old)


def test_tampered_or_foreign_tokens(pg):
    uid = add_user()
    token, sid = tokens.issue_session(uid)
    head, payload, sig = token.split(".")
    with pytest.raises(BadSignature):
        tokens.verify_session(f"{head}.{payload}.{sig[::-1]}")
    # a tool-key-signed token with session claims is not a session
    now = int(time.time())
    foreign = jwt.encode({"sub": uid, "sid": sid, "iat": now, "exp": now + 60},
                         keys.load_key(keys.TOOL), algorithm="HS256")
    with pytest.raises(BadSignature):
        tokens.verify_session(foreign)


def test_sid_bound_to_its_user(pg):
    """Even with the key, a sid opened for A does not authenticate B."""
    a, b = add_user(), add_user()
    _, sid_a = tokens.issue_session(a)
    now = int(time.time())
    swapped = jwt.encode({"sub": b, "sid": sid_a, "iat": now, "exp": now + 60},
                         keys.load_key(keys.SESSION), algorithm="HS256")
    with pytest.raises(SessionRevoked):
        tokens.verify_session(swapped)


def test_overlong_session_rejected(pg):
    uid = add_user()
    _, sid = tokens.issue_session(uid)
    now = int(time.time())
    long = jwt.encode({"sub": uid, "sid": sid, "iat": now, "exp": now + 30 * 86400},
                      keys.load_key(keys.SESSION), algorithm="HS256")
    with pytest.raises(TokenError):
        tokens.verify_session(long)


def _principal(uid: str, role: Role, dept: Dept, clearance: Level, groups: set[str]) -> Principal:
    return Principal(user_id=uid, name=uid, email=f"{uid}@abctech.example", dept=dept, role=role, title="t",
                     manager_id=None, clearance=clearance, groups=frozenset(groups))


def _audit_for(resource: str) -> list[dict]:
    with pgmod.connect_owner() as c:
        return c.execute("SELECT * FROM audit.log WHERE resource = %s ORDER BY seq", (resource,)).fetchall()


def test_revoke_all_user_sessions(pg):
    uid = add_user()
    admin = _principal(add_user(dept="it", role="platform_admin", clearance=2), Role.PLATFORM_ADMIN, Dept.IT,
                       Level.L2, {"all", "dept:it", "role:platform_admin"})
    t1, _ = tokens.issue_session(uid)
    t2, _ = tokens.issue_session(uid)
    assert tokens.revoke_user_sessions(admin, uid, reason="offboarding ticket 42") == 2
    for t in (t1, t2):
        with pytest.raises(SessionRevoked):
            tokens.verify_session(t)
    (row,) = _audit_for(f"user:{uid}")
    assert (row["event"], row["action"], row["decision"], row["actor"]) == ("login", "revoke_sessions", "allow",
                                                                             admin.user_id)
    assert row["details"] == {"revoked": 2}


def test_only_the_platform_admin_may_log_others_out(pg):
    victim = add_user(dept="it", role="security_auditor", clearance=2)
    token, _ = tokens.issue_session(victim)
    staff = _principal(add_user(), Role.EMPLOYEE, Dept.SALES, Level.L3, {"all", "dept:sales"})
    with pytest.raises(PermissionError):
        tokens.revoke_user_sessions(staff, victim, reason="because")
    # a forged role without the group, and no principal at all, are refused by the database too
    forged = _principal(staff.user_id, Role.PLATFORM_ADMIN, Dept.SALES, Level.L3, {"all"})
    with pytest.raises(PermissionError):
        tokens.revoke_user_sessions(forged, victim, reason="because")
    with pgmod.connect_app() as conn:
        with pytest.raises(Exception, match="only the platform admin"):
            conn.execute("SELECT core.session_revoke_user(%s)", (victim,))
    assert tokens.verify_session(token).sub == victim  # still logged in
    assert [(r["decision"], r["reason"]) for r in _audit_for(f"user:{victim}")] == [
        ("deny", "not_platform_admin"), ("deny", "refused_by_database")]


def test_owner_jobs_may_revoke_directly(pg):
    uid = add_user()
    token, _ = tokens.issue_session(uid)
    with pgmod.connect_owner() as c:
        assert c.execute("SELECT core.session_revoke_user(%s) AS n", (uid,)).fetchone()["n"] == 1
    with pytest.raises(SessionRevoked):
        tokens.verify_session(token)


def test_app_role_has_only_execute(pg):
    with pgmod.connect_app() as conn:
        for sql in ("INSERT INTO core.sessions (sid, user_id, expires_at) VALUES ('x', 'y', now())",
                    "UPDATE core.sessions SET revoked_at = NULL",
                    "DELETE FROM core.sessions"):
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(sql)
            conn.rollback()
        # ttl above 8 h is refused by the function itself
        uid = add_user()
        assert conn.execute("SELECT core.session_open('sid_long', %s, 999999) AS ok", (uid,)).fetchone()["ok"] is False


@pytest.mark.anyio
async def test_async_session_flow(pg):
    uid = add_user()
    token, sid = await tokens.aissue_session(uid)
    assert (await tokens.averify_session(token)).sid == sid
    assert await tokens.arevoke_session(sid, uid) is True
    with pytest.raises(SessionRevoked):
        await tokens.averify_session(token)
