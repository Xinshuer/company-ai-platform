"""audit.record / arecord / read_log: validation, privileges, survival of rolled-back transactions."""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from corpai import audit
from corpai.audit.chain import ZERO_HASH
from corpai.db import pg as pgmod
from corpai.ids import new_id
from corpai.types import Dept, Level, Role


def _row(seq: int) -> dict:
    with pgmod.connect_owner() as c:
        return c.execute("SELECT * FROM audit.log WHERE seq = %s", (seq,)).fetchone()


def test_record_stores_all_fields(pg):
    run_id = new_id("run")
    seq = audit.record("tool_call", actor="u0031", act_chain=["agent:sales", "tool:crm.get_account"],
                       action="read", resource="crm.account:A-0012", decision="allow", reason="owner rep",
                       route="local", level=Level.L3, approval_id=None, run_id=run_id,
                       details={"jti": "abc", "tokens": {"prompt_tokens": 12}, "note": "Grüße \"x\"\n"})
    row = _row(seq)
    assert row["event"] == "tool_call" and row["actor"] == "u0031"
    assert row["act_chain"] == ["agent:sales", "tool:crm.get_account"]
    assert row["level"] == 3 and row["route"] == "local" and row["run_id"] == run_id
    assert row["details"] == {"jti": "abc", "tokens": {"prompt_tokens": 12}, "note": "Grüße \"x\"\n"}
    assert len(bytes(row["hash"])) == 32 and len(bytes(row["prev_hash"])) == 32


def test_seq_increases_and_links(pg):
    a = audit.record("login", actor="u0001")
    b = audit.record("login", actor="u0002")
    assert b > a
    # Tests in one session run sequentially, so b chains directly onto a (seq may have gaps).
    assert bytes(_row(b)["prev_hash"]) == bytes(_row(a)["hash"])
    assert audit.verify_chain().ok


def test_first_row_prev_hash_is_zero(pg):
    audit.record("login", actor="u0001")
    with pgmod.connect_owner() as c:
        first = c.execute("SELECT prev_hash FROM audit.log ORDER BY seq LIMIT 1").fetchone()
    assert bytes(first["prev_hash"]) == ZERO_HASH


@pytest.mark.anyio
async def test_50_concurrent_arecord_make_a_linear_valid_chain(pg):
    run_id = new_id("run")
    seqs = await asyncio.gather(*[
        audit.arecord("retrieval", actor=f"u{i:04d}", run_id=run_id, details={"i": i}) for i in range(50)])
    assert len(set(seqs)) == 50
    with pgmod.connect_owner() as c:
        rows = c.execute("SELECT seq, prev_hash, hash FROM audit.log WHERE run_id = %s ORDER BY seq",
                         (run_id,)).fetchall()
        assert [r["seq"] for r in rows] == sorted(seqs)
        # Every row chains onto the row immediately before it in seq order: no forks.
        for r in rows:
            prev = c.execute("SELECT hash FROM audit.log WHERE seq < %s ORDER BY seq DESC LIMIT 1",
                             (r["seq"],)).fetchone()
            assert bytes(r["prev_hash"]) == bytes(prev["hash"])
        hashes = [bytes(r["hash"]) for r in rows]
        assert len(set(hashes)) == 50
    py, sql = audit.verify_chain(), audit.verify_chain_sql()
    assert py.ok and sql.ok and py == sql


def test_record_owner_job(pg):
    seq = audit.record_owner_job("synth", {"seed": 7, "small": True})
    row = _row(seq)
    assert row["event"] == "owner_job" and row["actor"] == "svc:synth"
    assert row["details"] == {"seed": 7, "small": True}
    with pytest.raises(ValueError):
        audit.record_owner_job("Bad Job; DROP TABLE")


# ---- validation -------------------------------------------------------------------------------

@pytest.mark.parametrize("event", ["", "Login", "logout", "model-call", "tool_call ", "DROP"])
def test_unknown_event_rejected(pg, event):
    with pytest.raises(ValueError):
        audit.record(event, actor="u0001")


@pytest.mark.parametrize("details", [
    {"token": "x"},
    {"Authorization": "x"},
    {"api_key": "x"},
    {"apiKey": "x"},
    {"X-Api-Key": "x"},
    {"APIKEY": "x"},
    {"password": "x"},
    {"secret": "x"},
    {"Cookie": "x"},
    {"session": "x"},
    {"access_token": "x"},
    {"sessionToken": "x"},
    {"outer": {"inner": {"PASSWORD": "x"}}},
    {"calls": [{"ok": 1}, {"headers": {"authorization": "x"}}]},
    {"note": "Bearer abcdefghijklmnop"},
    {"t": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MDAwMSJ9.sig"},
    {"k": "sk-0123456789abcdef0123"},
])
def test_credential_details_rejected(pg, details):
    with pytest.raises(ValueError, match="credentials must never"):
        audit.record("tool_call", actor="u0001", details=details)


def test_credential_error_does_not_echo_the_secret(pg):
    with pytest.raises(ValueError) as ei:
        audit.record("tool_call", actor="u0001", details={"session": "S3CR3T-VALUE"})
    assert "S3CR3T-VALUE" not in str(ei.value)


@pytest.mark.parametrize("details", [{"tokens": 120}, {"prompt_tokens": 5, "completion_tokens": 7},
                                     {"jti": "j-1"}, {"secretary": "Ms Example"}, {"cookies_policy": "v2"}])
def test_harmless_keys_allowed(pg, details):
    assert audit.record("model_call", actor="u0001", details=details) > 0


def test_token_in_reason_rejected(pg):
    with pytest.raises(ValueError):
        audit.record("tool_call", actor="u0001", reason="used Bearer abcdefghijklmnopqrstu")


@pytest.mark.parametrize("kwargs", [
    {"level": 5}, {"level": 0}, {"route": "cloud"}, {"actor": ""}, {"act_chain": "agent:x"},
    {"details": {"x": float("nan")}}, {"details": {"x": object()}},
])
def test_bad_fields_rejected(pg, kwargs):
    args = {"actor": "u0001"} | kwargs
    with pytest.raises((ValueError, TypeError)):
        audit.record("login", **args)


def _app_append(details: str, event: str = "tool_call"):
    with pgmod.connect_app(autocommit=True) as c:
        return c.execute("SELECT audit.append(%s, %s, '{}'::text[], NULL, NULL, NULL, NULL, NULL, NULL, "
                         "NULL, NULL, %s::jsonb)", (event, "u0001", details)).fetchone()


def test_database_rejects_credential_keys_even_without_python(pg):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _app_append('{"outer": [{"Api-Key": "x"}]}')
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _app_append('{"accessToken": "x"}')
    assert _app_append('{"tokens": 3}')


def test_database_rejects_unknown_event(pg):
    with pytest.raises(psycopg.errors.CheckViolation):
        _app_append("{}", event="made_up")


def test_append_refused_outside_read_committed(pg):
    with pgmod.connect_app(autocommit=True) as c:
        with pytest.raises(psycopg.errors.InvalidTransactionState):
            with c.transaction():
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                c.execute("SELECT audit.append('login', 'u0001')")


# ---- privileges -------------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM audit.log",
    "INSERT INTO audit.log (seq, ts, actor, event, prev_hash, hash) VALUES (1, now(), 'x', 'login', '', '')",
    "UPDATE audit.log SET reason = 'x'",
    "DELETE FROM audit.log",
    "TRUNCATE audit.log",
    "SELECT * FROM audit.verify_chain()",
    "SELECT nextval('audit.log_seq')",
])
def test_app_role_has_no_table_access(pg, auditor, sql):
    # Even with the auditor's session variables the table itself is closed to corpai_app.
    with pgmod.principal_tx_sync(auditor) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():  # savepoint, so the outer transaction stays usable
                c.execute(sql)


@pytest.mark.parametrize("sql", [
    "UPDATE audit.log SET reason = 'edited' WHERE seq = %(seq)s",
    "UPDATE audit.log SET reason = 'edited' WHERE false",
    "DELETE FROM audit.log WHERE seq = %(seq)s",
    "DELETE FROM audit.log WHERE false",
    "TRUNCATE audit.log",
])
def test_owner_cannot_update_delete_truncate(pg, sql):
    seq = audit.record("login", actor="u0001", reason="original")
    with pgmod.connect_owner() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():
                c.execute(sql, {"seq": seq})
    assert _row(seq)["reason"] == "original"


def test_owner_direct_insert_is_still_chained(pg):
    # Whatever hashes or seq an inserter supplies, the trigger replaces them under the lock.
    with pgmod.connect_owner() as c:
        r = c.execute("INSERT INTO audit.log (seq, ts, actor, event, prev_hash, hash) "
                      "VALUES (999999, '2000-01-01', 'svc:test', 'owner_job', %s, %s) RETURNING seq, ts",
                      (b"\x11" * 32, b"\x22" * 32)).fetchone()
    assert r["seq"] != 999999 and r["ts"].year > 2000
    assert audit.verify_chain().ok


# ---- read_log -----------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_read_log_works_for_auditor(pg, auditor):
    a = audit.record("export", actor="u0001", resource="report:r1")
    b = audit.record("export", actor="u0001", resource="report:r2")
    rows = await audit.read_log(auditor, after_seq=a - 1, limit=2)
    assert [r["seq"] for r in rows] == [a, b]
    assert isinstance(rows[0]["hash"], str) and len(rows[0]["hash"]) == 64
    assert len(await audit.read_log(auditor, after_seq=0, limit=1)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("role,dept", [(Role.EMPLOYEE, Dept.SALES), (Role.MANAGER, Dept.FINANCE),
                                       (Role.CEO, Dept.EXEC), (Role.PLATFORM_ADMIN, Dept.IT),
                                       (Role.SERVICE, Dept.IT)])
async def test_read_log_denies_non_auditors(pg, mkp, role, dept):
    with pytest.raises(PermissionError):
        await audit.read_log(mkp("u0100", role, dept, Level.L4))


@pytest.mark.anyio
async def test_read_log_denies_deactivated_auditor(pg, mkp):
    with pytest.raises(PermissionError):
        await audit.read_log(mkp("u0069", Role.SECURITY_AUDITOR, Dept.IT, Level.L2, active=False))


def test_database_enforces_auditor_only(pg, mkp):
    """Bypass the Python check: the SQL function itself refuses non-auditors."""
    cases = [
        mkp("u0100", Role.CEO, Dept.EXEC, Level.L4),
        mkp("u0101", Role.PLATFORM_ADMIN, Dept.IT, Level.L2),
        # right role name but not the group: a forged app.role alone is not enough
        mkp("u0102", Role.SECURITY_AUDITOR, Dept.IT, Level.L2, groups={"all"}),
        # a deactivated auditor gets no groups from principal_tx
        mkp("u0069", Role.SECURITY_AUDITOR, Dept.IT, Level.L2, active=False),
    ]
    for p in cases:
        with pgmod.principal_tx_sync(p) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():
                    c.execute("SELECT * FROM audit.read_log(0, 10)")
    with pgmod.connect_app(autocommit=True) as c:  # no principal at all
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM audit.read_log(0, 10)")


@pytest.mark.anyio
async def test_entry_survives_rolled_back_business_transaction(pg, mkp):
    p = mkp("u0031", Role.EMPLOYEE, Dept.SALES)
    marker = new_id("run")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with pgmod.principal_tx(p, run_id=marker) as conn:
            await conn.execute("SELECT 1")
            await audit.arecord("policy_decision", actor=p.user_id, decision="deny", run_id=marker)
            audit.record("policy_decision", actor=p.user_id, decision="deny", run_id=marker)
            raise Boom()
    with pgmod.connect_owner() as c:
        n = c.execute("SELECT count(*) AS n FROM audit.log WHERE run_id = %s", (marker,)).fetchone()["n"]
    assert n == 2
