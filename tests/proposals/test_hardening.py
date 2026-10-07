"""Review fixes on the proposal path (phase B): the SQL functions enforce the catalogue tier
themselves, nothing runs in a deactivated requester's name, a failed execution never leaves a
proposal 'approved' forever, and a requester always sees their own proposal whatever its level."""

from __future__ import annotations

import random

import psycopg
import pytest

from corpai import proposals
from corpai.db import pg
from corpai.identity import directory
from corpai.ids import new_id
from corpai.policy.catalogue import TOOL_CATALOGUE
from corpai.proposals import ProposalExpired, ProposalNotFound
from corpai.proposals import service as service_mod
from corpai.types import ToolTier
from tests.mcp.support import count, owner_exec, owner_rows

pytestmark = [pytest.mark.anyio, pytest.mark.security]

CONTACT = "charlotte.teunissen@havenmeer-distributie.example"


def _add_sales_rep() -> str:
    uid = f"u9{random.randrange(1000):03d}"
    owner_exec("DELETE FROM core.users WHERE user_id = %s", (uid,))
    owner_exec("INSERT INTO core.users (user_id, name, email, dept, role, title, manager_id, clearance, active)"
               " VALUES (%s, %s, %s, 'sales', 'employee', 'Account Executive', 'u0010', 3, true)",
               (uid, f"Temp Rep {uid}", f"{uid}@kestrelbyte.example"))
    return uid


async def _mail(p, **kw) -> proposals.Proposal:
    return await proposals.create(p, agent="sales_profile", run_id=new_id("run"), domain="crm", tool="send_email",
                                  params={"account_id": "A-0002", "to": CONTACT, "subject": "Pilot",
                                          "body": kw.pop("body", "Short follow-up on the pilot.")},
                                  summary=kw.pop("summary", "E-mail: Pilot"), level_floor=3, **kw)


# ---- the catalogue tier lives in SQL too ----------------------------------------------------------

def test_sql_tool_tiers_equal_the_python_catalogue(seeded):
    with pg.connect_owner() as conn:
        for domain, tools in TOOL_CATALOGUE.items():
            for tool, tier in tools.items():
                got = conn.execute("SELECT ops.tool_tier(%s, %s) AS t", (domain, tool)).fetchone()["t"]
                assert got == (None if tier is ToolTier.READ else int(tier)), (domain, tool)
        assert conn.execute("SELECT ops.tool_tier('crm', 'drop_tables') AS t").fetchone()["t"] is None


def _create_sql(conn, prop_id: str, tool: str, tier: int):
    text = '{"to":"x@y.example"}'
    return conn.execute(
        "SELECT * FROM ops.proposal_create(%s, 'sales_profile', '', '', 'crm', %s, %s::smallint, %s,"
        " encode(sha256(convert_to(%s, 'UTF8')), 'hex'), 'summary', 3::smallint, '{}'::text[])",
        (prop_id, tool, tier, text, text)).fetchone()


async def test_a_claimed_tier_2_cannot_lower_a_four_eyes_tool(personas):
    """A buggy or forged app-role caller files crm.send_email as tier 2: SQL stores tier 3, and the
    requester still cannot approve their own outbound mail."""
    staff = personas["sales_staff"]
    pid = new_id("prop")
    with pg.principal_tx_sync(staff) as conn:
        row = _create_sql(conn, pid, "send_email", 2)
    assert row["tier"] == 3
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pg.principal_tx_sync(staff) as conn:
            conn.execute("SELECT * FROM ops.proposal_decide(%s, 'approved', %s, 'self')",
                         (pid, row["params_hash"])).fetchall()


async def test_a_stored_low_tier_is_raised_at_decision_time(personas):
    """Even a row whose stored tier was tampered with is decided at the catalogue tier."""
    staff = personas["sales_staff"]
    prop = await _mail(staff)
    owner_exec("UPDATE ops.proposals SET tier = 2 WHERE id = %s", (prop.id,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pg.principal_tx_sync(staff) as conn:
            conn.execute("SELECT * FROM ops.proposal_decide(%s, 'approved', %s, 'self')",
                         (prop.id, prop.params_hash)).fetchall()


async def test_sql_refuses_a_tool_outside_the_write_catalogue(personas):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        with pg.principal_tx_sync(personas["sales_staff"]) as conn:
            _create_sql(conn, new_id("prop"), "get_account", 2)


# ---- deactivated requesters --------------------------------------------------------------------

async def test_a_deactivated_requesters_proposal_lapses_and_never_runs(seeded, personas):
    uid = _add_sales_rep()
    rep = await directory.get_principal(uid)
    prop = await _mail(rep)
    head = personas["sales_head"]
    assert prop.id in {p["proposal_id"] for p in await proposals.list_for(head, "to_approve")}
    owner_exec("UPDATE core.users SET active = false WHERE user_id = %s", (uid,))
    try:
        # gone from the inbox at once
        assert prop.id not in {p["proposal_id"] for p in await proposals.list_for(head, "to_approve")}
        outbox = count("ops.outbox")
        with pytest.raises(ProposalExpired):
            await proposals.approve(head, prop.id, params_hash=prop.params_hash, reason="looks fine")
        row = owner_rows("SELECT status, reason FROM ops.proposals WHERE id = %s", (prop.id,))[0]
        assert row == {"status": "expired", "reason": "requester no longer active"}
        assert count("ops.outbox") == outbox
        assert any(r["reason"] == "requester_inactive" and r["decision"] == "deny" for r in owner_rows(
            "SELECT reason, decision FROM audit.log WHERE approval_id = %s", (prop.id,)))
    finally:
        owner_exec("DELETE FROM core.users WHERE user_id = %s", (uid,))


async def test_sql_lets_the_proposal_lapse_even_if_python_is_bypassed(seeded, personas):
    uid = _add_sales_rep()
    prop = await _mail(await directory.get_principal(uid))
    owner_exec("UPDATE core.users SET active = false WHERE user_id = %s", (uid,))
    try:
        with pg.principal_tx_sync(personas["sales_head"]) as conn:
            row = conn.execute("SELECT * FROM ops.proposal_decide(%s, 'approved', %s, 'ok')",
                               (prop.id, prop.params_hash)).fetchone()
        assert row["status"] == "expired"
    finally:
        owner_exec("DELETE FROM core.users WHERE user_id = %s", (uid,))


async def test_the_executor_refuses_a_requester_deactivated_after_approval(seeded, personas):
    uid = _add_sales_rep()
    prop = await _mail(await directory.get_principal(uid))
    owner_exec("UPDATE ops.proposals SET status = 'approved', approver_id = 'u0010', decided_at = now()"
               " WHERE id = %s", (prop.id,))
    owner_exec("UPDATE core.users SET active = false WHERE user_id = %s", (uid,))
    try:
        outbox = count("ops.outbox")
        done = await proposals.execute(prop.id)
        assert done.status == "failed" and done.result == {"error": "requester_inactive"}
        assert count("ops.outbox") == outbox
    finally:
        owner_exec("DELETE FROM core.users WHERE user_id = %s", (uid,))


# ---- execution failures never leave a proposal 'approved' ------------------------------------------

async def test_an_executor_crash_marks_the_proposal_failed(personas, monkeypatch):
    prop = await _mail(personas["sales_staff"])
    outbox = count("ops.outbox")

    def broken(proposal_id: str):
        raise RuntimeError("owner connection lost")

    monkeypatch.setattr(service_mod, "execute_sync", broken)
    done = await proposals.approve(personas["sales_head"], prop.id, params_hash=prop.params_hash, reason="ok")
    assert done.status == "failed" and done.result == {"error": "executor_error", "type": "RuntimeError"}
    assert owner_rows("SELECT status FROM ops.proposals WHERE id = %s", (prop.id,))[0]["status"] == "failed"
    assert count("ops.outbox") == outbox


async def test_a_failing_audit_write_rolls_back_and_marks_failed(personas, monkeypatch):
    """The executor's writes and its audit row share one transaction: no audit, no mail, status failed."""
    prop = await _mail(personas["sales_staff"])
    outbox = count("ops.outbox")
    real = service_mod.audit.record

    def flaky(event, **kw):
        if event == "proposal_executed" and kw.get("decision") == "allow":
            raise RuntimeError("audit down")
        return real(event, **kw)

    monkeypatch.setattr(service_mod.audit, "record", flaky)
    done = await proposals.approve(personas["sales_head"], prop.id, params_hash=prop.params_hash, reason="ok")
    assert done.status == "failed" and done.result["error"] == "executor_error"
    assert count("ops.outbox") == outbox


async def test_interrupted_approvals_are_failed_at_startup(personas):
    prop = await _mail(personas["sales_staff"])
    owner_exec("UPDATE ops.proposals SET status = 'approved', approver_id = 'u0010',"
               " decided_at = now() - interval '1 hour' WHERE id = %s", (prop.id,))
    assert prop.id in proposals.fail_interrupted(older_than_s=60)
    row = owner_rows("SELECT status, result FROM ops.proposals WHERE id = %s", (prop.id,))[0]
    assert row["status"] == "failed" and row["result"] == {"error": "execution_interrupted"}


# ---- the requester always sees their own proposal ---------------------------------------------------

async def test_an_l4_proposal_stays_visible_and_withdrawable_for_its_l3_requester(personas):
    staff = personas["sales_staff"]
    prop = await proposals.create(staff, agent="sales_profile", run_id=new_id("run"), domain="crm",
                                  tool="log_interaction", params={"account_id": "A-0002", "kind": "note",
                                                                  "summary": "x"},
                                  summary="note", level_floor=4)
    assert prop.level == 4 and int(staff.clearance) == 3
    assert (await proposals.get(staff, prop.id)) is not None
    assert prop.id in {p["proposal_id"] for p in await proposals.list_for(staff, "mine")}
    withdrawn = await proposals.reject(staff, prop.id, reason="wrong account")
    assert withdrawn.status == "rejected"
    # nobody else gains anything: another rep still sees nothing
    other = await directory.get_principal("u0100")
    assert await proposals.get(other, prop.id) is None
    with pytest.raises(ProposalNotFound):
        await proposals.reject(other, prop.id, reason="x")
