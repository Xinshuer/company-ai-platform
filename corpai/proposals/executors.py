"""Executors: the only code that writes the simulated systems (decisions D8).

Each executor runs inside the owner-role transaction opened by `service.execute()`, after the
proposal was approved and its params hash re-checked. They re-validate everything they rely on
(the target row exists, the recipient belongs to the account, the entry balances), because
the database may have changed between proposal and approval. New rows get the §5.1 label of
their kind; their level is raised by classify_text() over the text the proposal put in.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Callable, Iterable

import psycopg

from corpai import clock
from corpai.classify import classify_text
from corpai.ids import new_id
from corpai.proposals.model import Proposal

Executor = Callable[[psycopg.Connection, Proposal, dict], dict]
EXECUTORS: dict[tuple[str, str], Executor] = {}


class ExecutorError(Exception):
    """A business precondition failed at execution time; the proposal becomes `failed`."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def executor(domain: str, tool: str) -> Callable[[Executor], Executor]:
    def deco(fn: Executor) -> Executor:
        EXECUTORS[(domain, tool)] = fn
        return fn
    return deco


# ---- helpers --------------------------------------------------------------------------------

def _level(*texts: Any, floor: int = 1) -> int:
    lvl = floor
    for t in texts:
        if t is None:
            continue
        s = t if isinstance(t, str) else json.dumps(t, ensure_ascii=False, sort_keys=True, default=str)
        lvl = max(lvl, int(classify_text(s).level))
    return lvl


def _next_id(conn: psycopg.Connection, table: str, column: str, prefix: str, width: int) -> str:
    """Next sequential business id like FP-1061 or C-0016. The table lock serialises executors so
    two approvals at once cannot pick the same number. `table`/`column` are code constants."""
    conn.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
    pattern = f"^{re.escape(prefix)}[0-9]+$"
    row = conn.execute(
        f"SELECT coalesce(max(substring({column} FROM %s)::bigint), 0) AS n FROM {table} WHERE {column} ~ %s",
        (f"^{re.escape(prefix)}([0-9]+)$", pattern)).fetchone()
    n = int(row["n"]) + 1
    if len(str(n)) > width:
        raise ExecutorError("id_space_exhausted", f"no free id left for {prefix}")
    return f"{prefix}{n:0{width}d}"


def _one(conn: psycopg.Connection, sql: str, params: Iterable[Any], what: str) -> dict:
    row = conn.execute(sql, tuple(params)).fetchone()
    if row is None:
        raise ExecutorError("not_found", f"{what} no longer exists")
    return row


def _contact_for(conn: psycopg.Connection, account_id: str, email: str) -> dict:
    """The recipient must be a CRM contact of that account (no mail to addresses a model made up,
    e.g. an attacker address planted in a web page)."""
    row = conn.execute("SELECT contact_id, email FROM crm.contacts WHERE account_id = %s AND lower(email) = lower(%s)",
                       (account_id, email)).fetchone()
    if row is None:
        raise ExecutorError("recipient_not_contact", "the recipient is not a contact of this account")
    return row


def _outbox(conn: psycopg.Connection, prop: Proposal, *, channel: str, recipient: str, subject: str, body: str,
            attachments: list, level_floor: int, dept: str, subject_ids: list[str]) -> str:
    out_id = new_id("out")
    level = _level(subject, body, floor=max(level_floor, int(prop.level)))
    conn.execute(
        "INSERT INTO ops.outbox (id, proposal_id, channel, recipient, subject, body, attachments, level, dept, owner_id,"
        " acl_groups, subject_ids) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)",
        (out_id, prop.id, channel, recipient, subject, body, json.dumps(attachments), level, dept, prop.requester_id,
         list(prop.acl_groups), subject_ids))
    return out_id


# ---- crm ------------------------------------------------------------------------------------

@executor("crm", "log_interaction")
def _crm_log_interaction(conn, prop: Proposal, params: dict) -> dict:
    acc = _one(conn, "SELECT account_id, owner_id, level, acl_groups FROM crm.accounts WHERE account_id = %s",
               [params["account_id"]], "the account")
    subject_ids: list[str] = []
    if params.get("contact_id"):
        _one(conn, "SELECT contact_id FROM crm.contacts WHERE contact_id = %s AND account_id = %s",
             [params["contact_id"], acc["account_id"]], "the contact")
        subject_ids = [params["contact_id"]]
    iid = _next_id(conn, "crm.interactions", "interaction_id", "I-", 5)
    conn.execute(
        "INSERT INTO crm.interactions (interaction_id, account_id, contact_id, kind, occurred_on, summary, sentiment,"
        " owner_id, level, dept, acl_groups, trust, subject_ids)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'sales', %s, 'internal', %s)",
        (iid, acc["account_id"], params.get("contact_id"), params["kind"], params["occurred_on"], params["summary"],
         params["sentiment"], acc["owner_id"], _level(params["summary"], floor=int(acc["level"])),
         acc["acl_groups"], subject_ids))
    return {"interaction_id": iid}


@executor("crm", "update_opportunity_stage")
def _crm_update_stage(conn, prop: Proposal, params: dict) -> dict:
    opp = _one(conn, "SELECT opp_id, stage FROM crm.opportunities WHERE opp_id = %s FOR UPDATE",
               [params["opp_id"]], "the opportunity")
    probability = {"closed_won": 100, "closed_lost": 0}.get(params["stage"])
    conn.execute("UPDATE crm.opportunities SET stage = %s, probability = coalesce(%s, probability) WHERE opp_id = %s",
                 (params["stage"], probability, opp["opp_id"]))
    return {"opp_id": opp["opp_id"], "from_stage": opp["stage"], "to_stage": params["stage"]}


@executor("crm", "send_email")
def _crm_send_email(conn, prop: Proposal, params: dict) -> dict:
    acc = _one(conn, "SELECT account_id, level FROM crm.accounts WHERE account_id = %s",
               [params["account_id"]], "the account")
    contact = _contact_for(conn, acc["account_id"], params["to"])
    out_id = _outbox(conn, prop, channel="email", recipient=contact["email"], subject=params["subject"],
                     body=params["body"], attachments=[], level_floor=int(acc["level"]), dept="sales",
                     subject_ids=[contact["contact_id"]])
    return {"outbox_id": out_id, "recipient": contact["email"]}


# ---- code -----------------------------------------------------------------------------------

@executor("code", "create_ticket")
def _code_create_ticket(conn, prop: Proposal, params: dict) -> dict:
    proj = _one(conn, "SELECT project_id FROM eng.projects WHERE project_id = %s", [params["project_id"]],
                "the project")
    tid = _next_id(conn, "eng.tickets", "ticket_id", "FP-", 4)
    today = clock.today()
    conn.execute(
        "INSERT INTO eng.tickets (ticket_id, project_id, title, description, type, status, assignee_id, created_on,"
        " updated_on, level, dept, owner_id, acl_groups, trust, subject_ids)"
        " VALUES (%s, %s, %s, %s, %s, 'open', NULL, %s, %s, %s, 'eng', %s, %s, 'internal', '{}')",
        (tid, proj["project_id"], params["title"], params["description"], params["type"], today, today,
         _level(params["title"], params["description"], floor=3), prop.requester_id,
         [f"proj:{proj['project_id']}", "head:eng"]))  # §5.1 eng_work ACL
    return {"ticket_id": tid}


@executor("code", "comment_ticket")
def _code_comment_ticket(conn, prop: Proposal, params: dict) -> dict:
    t = _one(conn, "SELECT ticket_id, level, acl_groups FROM eng.tickets WHERE ticket_id = %s FOR UPDATE",
             [params["ticket_id"]], "the ticket")
    cid = new_id("cmt")
    conn.execute(
        "INSERT INTO eng.ticket_comments (comment_id, ticket_id, author_id, body, proposal_id, level, dept, owner_id,"
        " acl_groups) VALUES (%s, %s, %s, %s, %s, %s, 'eng', %s, %s)",
        (cid, t["ticket_id"], prop.requester_id, params["comment"], prop.id,
         _level(params["comment"], floor=int(t["level"])), prop.requester_id, t["acl_groups"]))
    conn.execute("UPDATE eng.tickets SET updated_on = %s WHERE ticket_id = %s", (clock.today(), t["ticket_id"]))
    return {"ticket_id": t["ticket_id"], "comment_id": cid}


# ---- finance --------------------------------------------------------------------------------

_FIN_ACL = ["dept:finance", "head:finance", "role:ceo"]  # §5.1 ledger


@executor("finance", "post_journal_entry")
def _fin_post_journal_entry(conn, prop: Proposal, params: dict) -> dict:
    lines = params["lines"]
    debit = sum(Decimal(l["debit_eur"]) for l in lines)
    credit = sum(Decimal(l["credit_eur"]) for l in lines)
    if debit != credit or debit <= 0:
        raise ExecutorError("unbalanced", "the journal entry does not balance")
    codes = {l["account_code"] for l in lines}
    found = {r["account_code"] for r in conn.execute(
        "SELECT account_code FROM fin.chart_of_accounts WHERE account_code = ANY(%s)", (sorted(codes),)).fetchall()}
    if found != codes:
        raise ExecutorError("unknown_account", "an account code is not in the chart of accounts")
    txn_id = _next_id(conn, "fin.journal_entries", "txn_id", "T-", 5)
    level = _level(params["description"], floor=3)
    entry_ids = []
    for l in lines:
        eid = _next_id(conn, "fin.journal_entries", "entry_id", "JE-", 6)
        conn.execute(
            "INSERT INTO fin.journal_entries (entry_id, txn_id, posted_on, period, account_code, dept, description,"
            " debit_eur, credit_eur, project_id, customer_account_id, status, level, owner_id, acl_groups, trust,"
            " subject_ids) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, 'posted', %s, %s, %s, 'internal',"
            " '{}')",
            (eid, txn_id, params["posted_on"], params["posted_on"][:7], l["account_code"], l["dept"],
             params["description"], Decimal(l["debit_eur"]), Decimal(l["credit_eur"]), level, prop.requester_id,
             _FIN_ACL))
        entry_ids.append(eid)
    return {"txn_id": txn_id, "entry_ids": entry_ids}


# ---- hris -----------------------------------------------------------------------------------

@executor("hris", "create_requisition")
def _hris_create_requisition(conn, prop: Proposal, params: dict) -> dict:
    dept = params["dept"]
    # The hiring manager is the department's head (the CEO for exec), looked up here: tools never
    # take user ids as arguments (ENGINEERING security rule 1).
    role = "ceo" if dept == "exec" else "manager"
    head = _one(conn, "SELECT user_id FROM core.users WHERE dept = %s AND role = %s AND active ORDER BY user_id LIMIT 1",
                [dept, role], "the department head")
    req_id = _next_id(conn, "hr.requisitions", "req_id", "R-", 2)
    conn.execute(
        "INSERT INTO hr.requisitions (req_id, dept, title, headcount, status, opened_on, hiring_manager_id,"
        " recruiter_id, jd_text, hard_requirements, level, owner_id, acl_groups, trust, subject_ids)"
        " VALUES (%s, %s, %s, %s, 'open', %s, %s, %s, %s, %s::jsonb, %s, %s, %s, 'internal', '{}')",
        (req_id, dept, params["title"], params["headcount"], clock.today(), head["user_id"], prop.requester_id,
         params["jd_text"], json.dumps(params["hard_requirements"], ensure_ascii=False),
         _level(params["title"], params["jd_text"], params["hard_requirements"], floor=2), prop.requester_id,
         ["dept:hr", "head:hr", f"head:{dept}", "role:ceo", f"req:{req_id}"]))  # §5.1 requisition ACL
    # §5.1: the recruiter and the hiring manager are the requisition's panel (req:<id>).
    for uid in {prop.requester_id, head["user_id"]}:
        conn.execute("INSERT INTO core.memberships (user_id, grp) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (uid, f"req:{req_id}"))
    return {"req_id": req_id, "hiring_manager_id": head["user_id"]}


# ---- legal ----------------------------------------------------------------------------------

@executor("legal", "create_contract_draft")
def _legal_create_draft(conn, prop: Proposal, params: dict) -> dict:
    acc = _one(conn, "SELECT account_id, name FROM crm.accounts WHERE account_id = %s", [params["account_id"]],
               "the account")
    cid = _next_id(conn, "legal.contracts", "contract_id", "C-", 4)
    handler = prop.requester_id
    conn.execute(
        "INSERT INTO legal.contracts (contract_id, account_id, counterparty, contract_type, status, governing_law,"
        " handler_id, version, text, level, dept, owner_id, acl_groups, trust, subject_ids)"
        " VALUES (%s, %s, %s, %s, 'draft', %s, %s, 1, %s, %s, 'legal', %s, %s, 'internal', '{}')",
        (cid, acc["account_id"], acc["name"], params["contract_type"], params["governing_law"], handler,
         params["text"], _level(params["text"], floor=3), handler,
         [f"user:{handler}", "head:legal", "role:ceo"]))  # §5.1 contract ACL
    return {"contract_id": cid}


@executor("legal", "send_contract")
def _legal_send_contract(conn, prop: Proposal, params: dict) -> dict:
    c = _one(conn, "SELECT contract_id, account_id, status, version, level FROM legal.contracts WHERE contract_id = %s",
             [params["contract_id"]], "the contract")
    if c["status"] in ("expired", "terminated"):
        raise ExecutorError("contract_closed", "an expired or terminated contract cannot be sent")
    contact = _contact_for(conn, c["account_id"], params["to"])
    out_id = _outbox(conn, prop, channel="contract", recipient=contact["email"], subject=params["subject"],
                     body=params["body"], attachments=[{"contract_id": c["contract_id"], "version": c["version"]}],
                     level_floor=int(c["level"]), dept="legal", subject_ids=[contact["contact_id"]])
    return {"outbox_id": out_id, "contract_id": c["contract_id"], "recipient": contact["email"]}
