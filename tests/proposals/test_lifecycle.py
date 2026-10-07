"""approve / reject / expire / execute (architecture §11, design-review 二.12, decisions D8).

Four-eyes for tier 3, the approver must quote the exact params hash, executors re-check it, and
every step is audited without parameter values.
"""

from __future__ import annotations

import psycopg
import pytest

from corpai import proposals
from corpai.db import pg
from corpai.ids import new_id
from corpai.proposals import (
    ParamsHashMismatch,
    ProposalConflict,
    ProposalExpired,
    ProposalForbidden,
    ProposalNotFound,
)
from tests.mcp.support import WRITER_AGENT, count, owner_exec, owner_rows

pytestmark = [pytest.mark.anyio, pytest.mark.security]

CONTACT = "charlotte.teunissen@havenmeer-distributie.example"
SECRET_BODY = "Quarterly pilot plan for Havenmeer, discount ceiling 12 percent"


async def email_proposal(personas, run_id: str | None = None):
    return await proposals.create(personas["sales_staff"], agent="sales_profile", run_id=run_id or new_id("run"),
                                  domain="crm", tool="send_email",
                                  params={"account_id": "A-0002", "to": CONTACT, "subject": "Pilot",
                                          "body": SECRET_BODY},
                                  summary="E-mail to Charlotte Teunissen: Pilot", level_floor=3)


def audit_for(prop_id: str) -> list[dict]:
    return owner_rows("SELECT event, actor, decision, reason, details::text AS details FROM audit.log"
                      " WHERE approval_id = %s ORDER BY seq", (prop_id,))


async def test_tier3_self_approval_is_denied(personas):
    prop = await email_proposal(personas)
    assert prop.tier == 3 and prop.status == "pending"
    with pytest.raises(ProposalForbidden):
        await proposals.approve(personas["sales_staff"], prop.id, params_hash=prop.params_hash, reason="looks good")
    assert (await proposals.get(personas["sales_staff"], prop.id)).status == "pending"
    rows = audit_for(prop.id)
    assert any(r["event"] == "proposal_approved" and r["decision"] == "deny" and r["reason"] == "approve-t3-four-eyes"
               for r in rows)


async def test_four_eyes_is_enforced_in_sql_too(personas):
    """Even if Python were bypassed, ops.proposal_decide refuses the requester on tier 3."""
    prop = await email_proposal(personas)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pg.principal_tx_sync(personas["sales_staff"]) as conn:
            conn.execute("SELECT * FROM ops.proposal_decide(%s, 'approved', %s, 'self')",
                         (prop.id, prop.params_hash)).fetchall()


async def test_right_head_approves_and_the_outbox_gets_the_mail(personas):
    run_id = new_id("run")
    prop = await email_proposal(personas, run_id)
    outbox_before = count("ops.outbox")
    done = await proposals.approve(personas["sales_head"], prop.id, params_hash=prop.params_hash,
                                   reason="checked recipient and text")
    assert done.status == "executed" and done.approver_id == "u0010"
    assert count("ops.outbox") == outbox_before + 1
    mail = owner_rows("SELECT * FROM ops.outbox WHERE proposal_id = %s", (prop.id,))[0]
    assert mail["channel"] == "email" and mail["recipient"] == CONTACT and mail["body"] == SECRET_BODY
    assert mail["level"] >= 3 and set(mail["acl_groups"]) == {"user:u0011", "head:sales", "role:ceo"}
    events = [(r["event"], r["decision"]) for r in audit_for(prop.id)]
    assert ("proposal_created", "allow") in events and ("proposal_approved", "allow") in events
    assert ("proposal_executed", "allow") in events
    for r in audit_for(prop.id):  # audit rows carry ids and hashes, never the mail
        assert SECRET_BODY not in r["details"] and CONTACT not in r["details"] and "Pilot" not in r["details"]
    jobs = owner_rows("SELECT details::text AS d FROM audit.log WHERE event = 'owner_job' AND actor = 'svc:executor'"
                      " ORDER BY seq DESC LIMIT 5")
    assert any(prop.id in j["d"] for j in jobs)
    # the requester reads the result through RLS
    mine = await proposals.get(personas["sales_staff"], prop.id)
    assert mine.status == "executed" and mine.result["outbox_id"] == mail["id"]


async def test_head_of_another_department_cannot_see_or_approve(personas):
    prop = await email_proposal(personas)
    assert await proposals.get(personas["eng_head"], prop.id) is None
    with pytest.raises(ProposalNotFound):
        await proposals.approve(personas["eng_head"], prop.id, params_hash=prop.params_hash, reason="x")
    with pytest.raises(ProposalNotFound):
        await proposals.reject(personas["eng_staff"], prop.id, reason="x")


async def test_ceo_may_approve_tier3(personas):
    prop = await email_proposal(personas)
    done = await proposals.approve(personas["ceo"], prop.id, params_hash=prop.params_hash, reason="ok")
    assert done.status == "executed"


async def test_hash_mismatch_is_refused_and_nothing_runs(personas):
    prop = await email_proposal(personas)
    outbox_before = count("ops.outbox")
    with pytest.raises(ParamsHashMismatch):
        await proposals.approve(personas["sales_head"], prop.id, params_hash="0" * 64, reason="ok")
    assert (await proposals.get(personas["sales_head"], prop.id)).status == "pending"
    assert count("ops.outbox") == outbox_before
    # the SQL function refuses it as well
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        with pg.principal_tx_sync(personas["sales_head"]) as conn:
            conn.execute("SELECT * FROM ops.proposal_decide(%s, 'approved', %s, 'x')", (prop.id, "f" * 64)).fetchall()


async def test_reject_then_approve_is_a_conflict(personas):
    prop = await email_proposal(personas)
    rejected = await proposals.reject(personas["sales_head"], prop.id, reason="wrong contact")
    assert rejected.status == "rejected" and rejected.reason == "wrong contact"
    with pytest.raises(ProposalConflict):
        await proposals.approve(personas["sales_head"], prop.id, params_hash=prop.params_hash, reason="changed mind")
    assert any(r["event"] == "proposal_rejected" and r["decision"] == "allow" for r in audit_for(prop.id))


async def test_requester_may_withdraw_own_tier3(personas):
    prop = await email_proposal(personas)
    withdrawn = await proposals.reject(personas["sales_staff"], prop.id, reason="sent by phone instead")
    assert withdrawn.status == "rejected"


async def test_expired_proposal_cannot_be_approved(personas):
    prop = await email_proposal(personas)
    owner_exec("UPDATE ops.proposals SET expires_at = now() - interval '1 minute' WHERE id = %s", (prop.id,))
    with pytest.raises(ProposalExpired):
        await proposals.approve(personas["sales_head"], prop.id, params_hash=prop.params_hash, reason="late")
    assert owner_rows("SELECT status FROM ops.proposals WHERE id = %s", (prop.id,))[0]["status"] == "expired"
    assert prop.id not in {p["proposal_id"] for p in await proposals.list_for(personas["sales_head"], "to_approve")}


async def test_new_proposals_expire_after_seven_days(personas):
    prop = await email_proposal(personas)
    days = (prop.expires_at - prop.created_at).total_seconds() / 86400
    assert abs(days - 7) < 0.01


async def test_executor_rechecks_the_params(personas):
    """Someone edits the stored params after approval: the executor refuses and writes nothing."""
    prop = await email_proposal(personas)
    outbox_before = count("ops.outbox")
    owner_exec("UPDATE ops.proposals SET status = 'approved', approver_id = 'u0010',"
               " params = jsonb_set(params, '{to}', '\"collector@exfil-drop.example\"') WHERE id = %s", (prop.id,))
    done = await proposals.execute(prop.id)
    assert done.status == "failed" and done.result == {"error": "params_hash_mismatch"}
    assert count("ops.outbox") == outbox_before
    # and the stored text cannot drift from its hash at all
    with pytest.raises(psycopg.errors.CheckViolation):
        owner_exec("UPDATE ops.proposals SET params_text = replace(params_text, 'Pilot', 'Pwned') WHERE id = %s",
                   (prop.id,))


async def test_execute_refuses_unapproved(personas):
    prop = await email_proposal(personas)
    with pytest.raises(ProposalConflict):
        await proposals.execute(prop.id)


async def test_executor_refuses_a_recipient_that_is_not_a_contact(personas):
    """Defence in depth behind the tool's own check: the executor looks the recipient up again."""
    prop = await proposals.create(personas["sales_staff"], agent="sales_profile", run_id=new_id("run"), domain="crm",
                                  tool="send_email", params={"account_id": "A-0002", "to": "leads@exfil-drop.example",
                                                             "subject": "x", "body": "y"}, summary="test")
    done = await proposals.approve(personas["sales_head"], prop.id, params_hash=prop.params_hash, reason="ok")
    assert done.status == "failed" and done.result["error"] == "recipient_not_contact"
    assert owner_rows("SELECT count(*) AS n FROM ops.outbox WHERE proposal_id = %s", (prop.id,))[0]["n"] == 0


async def test_lists(personas):
    prop = await email_proposal(personas)
    mine = await proposals.list_for(personas["sales_staff"], "mine")
    entry = next(p for p in mine if p["proposal_id"] == prop.id)
    assert entry["can_approve"] is False and "second person" in entry["why_not"]
    assert entry["params"]["body"] == SECRET_BODY and entry["params_hash"] == prop.params_hash
    assert entry["requester"] == {"user_id": "u0011", "name": personas["sales_staff"].name}
    assert prop.id not in {p["proposal_id"] for p in await proposals.list_for(personas["sales_staff"], "to_approve")}
    head = await proposals.list_for(personas["sales_head"], "to_approve")
    assert prop.id in {p["proposal_id"] for p in head if p["can_approve"]}
    assert prop.id not in {p["proposal_id"] for p in await proposals.list_for(personas["finance_head"], "to_approve")}


async def test_app_role_cannot_write_proposals_directly(personas):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pg.principal_tx_sync(personas["sales_head"]) as conn:
            conn.execute("UPDATE ops.proposals SET status = 'approved'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pg.principal_tx_sync(personas["sales_head"]) as conn:
            conn.execute("INSERT INTO ops.outbox (id, proposal_id, channel, recipient, subject, body, level,"
                         " acl_groups) VALUES ('x', 'y', 'email', 'a@b.example', 's', 'b', 1, '{all}')")


async def test_create_without_a_principal_is_refused_in_sql(seeded):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pg.connect_app() as conn:
            conn.execute("SELECT * FROM ops.proposal_create('prop_aaaaaaaaaaaaaaaaaaaa', 'qa', '', '', 'crm',"
                         " 'send_email', 3::smallint, '{}', encode(sha256(convert_to('{}', 'UTF8')), 'hex'), 's',"
                         " 3::smallint, '{}'::text[])").fetchall()


# ---- tier 2 and every executor ---------------------------------------------------------------

async def _approve_own_tier2(call, persona: str, agent: str, domain: str, tool: str, **args):
    env = await call(persona, agent, domain, tool, **args)
    assert env.ok and env.tier == 2, env
    prop = await proposals.get(call.personas[persona], env.proposal_id)
    done = await proposals.approve(call.personas[persona], prop.id, params_hash=prop.params_hash,
                                   reason="confirmed by requester")
    assert done.status == "executed", done.result
    return done


async def test_tier2_requester_confirms_and_interaction_is_logged(call):
    done = await _approve_own_tier2(call, "sales_staff", "sales_profile", "crm", "log_interaction",
                                    account_id="A-0002", kind="meeting", summary="Pilot kickoff agreed.",
                                    contact_id="CT-00005")
    iid = done.result["interaction_id"]
    row = owner_rows("SELECT * FROM crm.interactions WHERE interaction_id = %s", (iid,))[0]
    assert row["account_id"] == "A-0002" and row["owner_id"] == "u0011" and row["level"] == 3
    assert set(row["acl_groups"]) == {"user:u0011", "head:sales", "role:ceo", "grant:crm:A-0002"}
    seen = await call("sales_staff", "sales_profile", "crm", "get_interactions", account_id="A-0002", limit=100)
    assert iid in {i["interaction_id"] for i in seen.data["interactions"]}


async def test_tier2_other_staff_cannot_confirm(call, personas):
    env = await call("sales_staff", "sales_profile", "crm", "update_opportunity_stage", opp_id="O-0002",
                     stage="negotiation")
    from corpai.identity import directory

    rep = await directory.get_principal("u0100")
    with pytest.raises(ProposalNotFound):  # another rep cannot even see it
        await proposals.approve(rep, env.proposal_id, params_hash="0" * 64, reason="x")


async def test_stage_update_executor(call):
    await _approve_own_tier2(call, "sales_staff", "sales_profile", "crm", "update_opportunity_stage",
                             opp_id="O-0002", stage="closed_won")
    row = owner_rows("SELECT stage, probability FROM crm.opportunities WHERE opp_id = 'O-0002'")[0]
    assert row == {"stage": "closed_won", "probability": 100}


async def test_ticket_and_comment_executors(call, writer_agent):
    done = await _approve_own_tier2(call, "eng_staff", WRITER_AGENT, "code", "create_ticket", project_id="P-01",
                                    title="Route export slow", description="Takes 40 s for 300 stops.")
    tid = done.result["ticket_id"]
    t = owner_rows("SELECT * FROM eng.tickets WHERE ticket_id = %s", (tid,))[0]
    assert t["status"] == "open" and set(t["acl_groups"]) == {"proj:P-01", "head:eng"}
    await _approve_own_tier2(call, "eng_staff", WRITER_AGENT, "code", "comment_ticket", ticket_id=tid,
                             comment="Profiling shows N+1 queries.")
    env = await call("eng_staff", WRITER_AGENT, "code", "get_ticket", ticket_id=tid)
    assert env.ok and env.data["ticket"]["comments"][0]["body"] == "Profiling shows N+1 queries."


async def test_journal_entry_needs_finance_head_and_posts(call, writer_agent, personas):
    env = await call("finance_staff", WRITER_AGENT, "finance", "post_journal_entry", posted_on="2026-09-30",
                     description="Accrual for audit fees",
                     lines=[{"account_code": "6900", "debit_eur": "500.00"}, {"account_code": "3700", "credit_eur": "500"}])
    assert env.tier == 3
    prop = await proposals.get(personas["finance_head"], env.proposal_id)
    with pytest.raises(ProposalForbidden):
        await proposals.approve(personas["finance_staff"], prop.id, params_hash=prop.params_hash, reason="self")
    done = await proposals.approve(personas["finance_head"], prop.id, params_hash=prop.params_hash, reason="ok")
    assert done.status == "executed"
    lines = owner_rows("SELECT * FROM fin.journal_entries WHERE txn_id = %s ORDER BY entry_id", (done.result["txn_id"],))
    assert len(lines) == 2 and {l["status"] for l in lines} == {"posted"}
    assert sum(l["debit_eur"] for l in lines) == sum(l["credit_eur"] for l in lines) == 500
    assert lines[0]["period"] == "2026-09"


async def test_requisition_executor_sets_panel(call):
    done = await _approve_own_tier2(call, "hr_staff", "hr_headcount", "hris", "create_requisition", dept="legal",
                                    title="Paralegal", headcount=1, jd_text="Contract admin.")
    rid = done.result["req_id"]
    r = owner_rows("SELECT * FROM hr.requisitions WHERE req_id = %s", (rid,))[0]
    assert r["hiring_manager_id"] == "u0030" and r["recruiter_id"] == "u0051" and r["level"] == 2
    panel = {m["user_id"] for m in owner_rows("SELECT user_id FROM core.memberships WHERE grp = %s", (f"req:{rid}",))}
    assert panel == {"u0030", "u0051"}


async def test_contract_draft_and_send(call, personas):
    done = await _approve_own_tier2(call, "legal_staff", "legal_review", "legal", "create_contract_draft",
                                    account_id="A-0002", contract_type="DPA", text="Data processing terms ...")
    cid = done.result["contract_id"]
    c = owner_rows("SELECT * FROM legal.contracts WHERE contract_id = %s", (cid,))[0]
    assert c["status"] == "draft" and c["handler_id"] == "u0031" and c["counterparty"] == "Havenmeer Distributie B.V."
    env = await call("legal_staff", "legal_review", "legal", "send_contract", contract_id=cid, to=CONTACT,
                     subject="DPA for signature", body="Please sign.")
    assert env.tier == 3
    prop = await proposals.get(personas["legal_head"], env.proposal_id)
    sent = await proposals.approve(personas["legal_head"], prop.id, params_hash=prop.params_hash, reason="ok")
    assert sent.status == "executed"
    out = owner_rows("SELECT * FROM ops.outbox WHERE proposal_id = %s", (prop.id,))[0]
    assert out["channel"] == "contract" and out["attachments"] == [{"contract_id": cid, "version": 1}]
