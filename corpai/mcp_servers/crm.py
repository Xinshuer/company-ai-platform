"""crm server: accounts, contacts, opportunities, interactions (architecture §11).

Rows are L3 with acl {user:<owner rep>, head:sales, role:ceo, grant:crm:<account>} (§5.1), so a
rep sees only their own accounts, through RLS and decide() alike.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context

from corpai import clock
from corpai.mcp_servers._common import (
    check_choice,
    check_date,
    check_email,
    check_id,
    check_limit,
    check_text,
    fetch_all,
    fetch_one,
    opt_choice,
    opt_id,
    search_text,
    tx,
)
from corpai.mcp_servers._envelope import NOT_VISIBLE, Collector, run_tool
from corpai.mcp_servers._propose import file_proposal, may_propose_on, refused

DOMAIN = "crm"
ACCOUNT_FIELDS = ["account_id", "name", "domain", "industry", "country", "city", "size_band", "employees",
                  "fleet_size", "owner_id", "tier", "arr_eur", "status", "created_on"]
CONTACT_FIELDS = ["contact_id", "account_id", "name", "title", "email", "phone"]
OPP_FIELDS = ["opp_id", "account_id", "name", "stage", "amount_eur", "close_date", "probability", "owner_id"]
INTERACTION_FIELDS = ["interaction_id", "account_id", "contact_id", "kind", "occurred_on", "summary", "sentiment",
                      "owner_id"]
STAGES = ("prospecting", "qualification", "proposal", "negotiation", "closed_won", "closed_lost")
KINDS = ("email", "call", "meeting", "demo")
SENTIMENTS = ("positive", "neutral", "negative")


def _not_visible(col: Collector, rid: str) -> dict:
    col.deny(rid, NOT_VISIBLE)
    return col.error("not_found_or_denied", "no such record, or you have no permission to see it")


async def search_accounts(ctx: Context, query: str = "", limit: int = 20) -> dict:
    """Accounts you may see whose name, domain, industry or city contains `query`."""
    async def body(col: Collector) -> dict:
        q, lim = search_text(query), check_limit(limit, default=20, maximum=50)
        async with tx(col) as conn:
            rows = await fetch_all(conn,
                                   "SELECT * FROM crm.accounts WHERE %s = '' OR name ILIKE '%%' || %s || '%%'"
                                   " OR domain ILIKE '%%' || %s || '%%' OR industry ILIKE '%%' || %s || '%%'"
                                   " OR city ILIKE '%%' || %s || '%%' ORDER BY account_id LIMIT %s",
                                   (q, q, q, q, q, lim))
        accounts = [a for r in rows if (a := col.row("crm.account", r["account_id"], r, ACCOUNT_FIELDS))]
        return col.envelope({"accounts": accounts, "count": len(accounts)})
    return await run_tool(ctx, DOMAIN, "search_accounts", body)


async def get_account(ctx: Context, account_id: str) -> dict:
    """One account with its contacts."""
    async def body(col: Collector) -> dict:
        aid = check_id("account_id", account_id)
        col.resource = f"crm.account:{aid}"
        async with tx(col) as conn:
            acc = await fetch_one(conn, "SELECT * FROM crm.accounts WHERE account_id = %s", (aid,))
            contacts = await fetch_all(conn, "SELECT * FROM crm.contacts WHERE account_id = %s ORDER BY contact_id",
                                       (aid,))
        account = col.row("crm.account", aid, acc, ACCOUNT_FIELDS) if acc else None
        if account is None:
            return _not_visible(col, f"crm.account:{aid}")
        account["contacts"] = [c for r in contacts if (c := col.row("crm.contact", r["contact_id"], r,
                                                                     CONTACT_FIELDS))]
        return col.envelope({"account": account})
    return await run_tool(ctx, DOMAIN, "get_account", body)


async def list_opportunities(ctx: Context, account_id: str | None = None, stage: str | None = None,
                             limit: int = 50) -> dict:
    """Opportunities you may see, optionally for one account or stage."""
    async def body(col: Collector) -> dict:
        aid = opt_id("account_id", account_id)
        st = opt_choice("stage", stage, STAGES)
        lim = check_limit(limit, default=50, maximum=200)
        col.resource = f"crm.account:{aid}" if aid else None
        async with tx(col) as conn:
            rows = await fetch_all(conn,
                                   "SELECT * FROM crm.opportunities WHERE (%s::text IS NULL OR account_id = %s)"
                                   " AND (%s::text IS NULL OR stage = %s) ORDER BY close_date, opp_id LIMIT %s",
                                   (aid, aid, st, st, lim))
            if aid and not rows and not await fetch_one(conn, "SELECT 1 FROM crm.accounts WHERE account_id = %s",
                                                        (aid,)):
                return _not_visible(col, f"crm.account:{aid}")
        opps = [o for r in rows if (o := col.row("crm.opportunity", r["opp_id"], r, OPP_FIELDS))]
        return col.envelope({"opportunities": opps, "count": len(opps)})
    return await run_tool(ctx, DOMAIN, "list_opportunities", body)


async def get_interactions(ctx: Context, account_id: str, limit: int = 20) -> dict:
    """The latest interactions with one account."""
    async def body(col: Collector) -> dict:
        aid = check_id("account_id", account_id)
        lim = check_limit(limit, default=20, maximum=100)
        col.resource = f"crm.account:{aid}"
        async with tx(col) as conn:
            if not await fetch_one(conn, "SELECT 1 FROM crm.accounts WHERE account_id = %s", (aid,)):
                return _not_visible(col, f"crm.account:{aid}")
            rows = await fetch_all(conn, "SELECT * FROM crm.interactions WHERE account_id = %s"
                                         " ORDER BY occurred_on DESC, interaction_id DESC LIMIT %s", (aid, lim))
        items = [i for r in rows if (i := col.row("crm.interaction", r["interaction_id"], r, INTERACTION_FIELDS))]
        return col.envelope({"account_id": aid, "interactions": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "get_interactions", body)


# ---- write tools: proposals only ------------------------------------------------------------

async def log_interaction(ctx: Context, account_id: str, kind: str, summary: str, contact_id: str | None = None,
                          sentiment: str = "neutral", occurred_on: str | None = None) -> dict:
    """Propose logging an interaction (tier 2: you confirm it yourself)."""
    async def body(col: Collector) -> dict:
        aid = check_id("account_id", account_id)
        params = {"account_id": aid, "kind": check_choice("kind", kind, KINDS),
                  "summary": check_text("summary", summary, max_len=2000),
                  "contact_id": opt_id("contact_id", contact_id),
                  "sentiment": check_choice("sentiment", sentiment, SENTIMENTS),
                  "occurred_on": (check_date("occurred_on", occurred_on) if occurred_on else clock.today()).isoformat()}
        col.resource = f"crm.account:{aid}"
        async with tx(col) as conn:
            acc = await fetch_one(conn, "SELECT * FROM crm.accounts WHERE account_id = %s", (aid,))
            contact = None
            if params["contact_id"]:
                contact = await fetch_one(conn, "SELECT * FROM crm.contacts WHERE contact_id = %s AND account_id = %s",
                                          (params["contact_id"], aid))
        if not may_propose_on(col, "crm.account", aid, acc):
            return refused(col)
        if params["contact_id"] and not may_propose_on(col, "crm.contact", params["contact_id"], contact):
            return refused(col)
        summary_text = f"Log {params['kind']} with {acc['name']} on {params['occurred_on']}"
        return await file_proposal(col, tool="log_interaction", params=params, summary=summary_text,
                                   level_floor=int(acc["level"]),
                                   subject_ids=[params["contact_id"]] if params["contact_id"] else [])
    return await run_tool(ctx, DOMAIN, "log_interaction", body)


async def update_opportunity_stage(ctx: Context, opp_id: str, stage: str) -> dict:
    """Propose moving an opportunity to another stage (tier 2)."""
    async def body(col: Collector) -> dict:
        oid = check_id("opp_id", opp_id)
        st = check_choice("stage", stage, STAGES)
        col.resource = f"crm.opportunity:{oid}"
        async with tx(col) as conn:
            opp = await fetch_one(conn, "SELECT * FROM crm.opportunities WHERE opp_id = %s", (oid,))
        if not may_propose_on(col, "crm.opportunity", oid, opp):
            return refused(col)
        return await file_proposal(col, tool="update_opportunity_stage", params={"opp_id": oid, "stage": st},
                                   summary=f"Move opportunity {oid} from {opp['stage']} to {st}",
                                   level_floor=int(opp["level"]))
    return await run_tool(ctx, DOMAIN, "update_opportunity_stage", body)


async def send_email(ctx: Context, account_id: str, to: str, subject: str, body: str) -> dict:
    """Propose an e-mail to a contact of the account (tier 3: another person must approve)."""
    async def run(col: Collector) -> dict:
        aid = check_id("account_id", account_id)
        rcpt = check_email("to", to)
        params = {"account_id": aid, "to": rcpt, "subject": check_text("subject", subject, max_len=300),
                  "body": check_text("body", body, max_len=20000)}
        col.resource = f"crm.account:{aid}"
        async with tx(col) as conn:
            acc = await fetch_one(conn, "SELECT * FROM crm.accounts WHERE account_id = %s", (aid,))
            contact = await fetch_one(conn, "SELECT * FROM crm.contacts WHERE account_id = %s AND lower(email) = lower(%s)",
                                      (aid, rcpt))
        if not may_propose_on(col, "crm.account", aid, acc):
            return refused(col)
        # Outbound mail goes only to a known contact of this account: an address taken from a web
        # page or a model's imagination (prompt injection) is refused before anyone is asked.
        if contact is None or not may_propose_on(col, "crm.contact", contact["contact_id"], contact):
            col.deny("crm.send_email:recipient", "the recipient is not a contact of this account")
            return col.error("recipient_not_contact", "send e-mail only to a contact of this account")
        return await file_proposal(col, tool="send_email", params=params,
                                   summary=f"E-mail to {contact['name']} <{contact['email']}> ({acc['name']}): "
                                           f"{params['subject']}",
                                   level_floor=int(acc["level"]), subject_ids=[contact["contact_id"]])
    return await run_tool(ctx, DOMAIN, "send_email", run)


TOOLS = [search_accounts, get_account, list_opportunities, get_interactions, log_interaction,
         update_opportunity_stage, send_email]
