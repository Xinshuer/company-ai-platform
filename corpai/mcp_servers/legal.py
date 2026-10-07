"""legal server: contracts, expiry, clause library, regulations (architecture §11).

Contracts are L3 for the handling lawyer, head:legal and the CEO; the regulation library is
public (L1, acl all, trust reviewed). "Expiring" is computed from clock.today() (simulation
date), never the OS clock, so planted answers are stable.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from mcp.server.mcpserver import Context

from corpai import clock
from corpai.mcp_servers._common import (
    check_choice,
    check_email,
    check_id,
    check_limit,
    check_period,
    check_text,
    fetch_all,
    fetch_one,
    opt_choice,
    opt_id,
    search_text,
    tx,
)
from corpai.mcp_servers._envelope import NOT_VISIBLE, Collector, ToolArgError, run_tool
from corpai.mcp_servers._propose import file_proposal, may_propose_new, may_propose_on, refused
from corpai.types import Dept, Label, Level

DOMAIN = "legal"
CONTRACT_LIST_FIELDS = ["contract_id", "account_id", "counterparty", "contract_type", "status", "signed_on",
                        "effective_on", "expires_on", "auto_renew", "notice_days", "governing_law", "has_dpa_clause",
                        "processes_personal_data", "liability_cap_eur", "handler_id", "version"]
CONTRACT_FIELDS = CONTRACT_LIST_FIELDS + ["text"]
CONTRACT_STATUSES = ("draft", "negotiation", "signed", "expired", "terminated")
CONTRACT_TYPES = ("MSA", "DPA", "NDA", "SOW", "order_form")
CLAUSE_FIELDS = ["clause_id", "clause_type", "position", "text"]
REG_FIELDS = ["reg_id", "title", "status", "summary", "source_uri"]
MAX_WITHIN_DAYS = 3660
_CLAUSE_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


async def search_contracts(ctx: Context, query: str = "", status: str | None = None, account_id: str | None = None,
                           limit: int = 20) -> dict:
    """Contracts you may see (structured fields only; use get_contract for the text)."""
    async def body(col: Collector) -> dict:
        q, lim = search_text(query), check_limit(limit, default=20, maximum=100)
        st = opt_choice("status", status, CONTRACT_STATUSES)
        aid = opt_id("account_id", account_id)
        async with tx(col) as conn:
            rows = await fetch_all(conn,
                                   "SELECT * FROM legal.contracts WHERE (%s = '' OR counterparty ILIKE '%%' || %s || '%%'"
                                   " OR contract_id ILIKE '%%' || %s || '%%' OR contract_type ILIKE '%%' || %s || '%%')"
                                   " AND (%s::text IS NULL OR status = %s) AND (%s::text IS NULL OR account_id = %s)"
                                   " ORDER BY contract_id LIMIT %s", (q, q, q, q, st, st, aid, aid, lim))
        items = [c for r in rows if (c := col.row("legal.contract", r["contract_id"], r, CONTRACT_LIST_FIELDS))]
        return col.envelope({"contracts": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "search_contracts", body)


async def get_contract(ctx: Context, contract_id: str) -> dict:
    """One contract with its full text. Uploaded counterparty drafts are untrusted (see label.trust)."""
    async def body(col: Collector) -> dict:
        cid = check_id("contract_id", contract_id)
        col.resource = f"legal.contract:{cid}"
        async with tx(col) as conn:
            row = await fetch_one(conn, "SELECT * FROM legal.contracts WHERE contract_id = %s", (cid,))
        c = col.row("legal.contract", cid, row, CONTRACT_FIELDS) if row else None
        if c is None:
            col.deny(f"legal.contract:{cid}", NOT_VISIBLE)
            return col.error("not_found_or_denied", "no such contract, or you have no permission to see it")
        return col.envelope({"contract": c})
    return await run_tool(ctx, DOMAIN, "get_contract", body)


def _window(within_days: int | None, month: str | None) -> tuple[date, date]:
    today = clock.today()
    if month is not None:
        if within_days is not None:
            raise ToolArgError("give either within_days or month, not both")
        y, m = (int(x) for x in check_period("month", month).split("-"))
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    days = 30 if within_days is None else within_days
    if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= MAX_WITHIN_DAYS:
        raise ToolArgError(f"within_days must be an integer between 0 and {MAX_WITHIN_DAYS}")
    return today, today + timedelta(days=days)


async def contracts_expiring(ctx: Context, within_days: int | None = None, month: str | None = None) -> dict:
    """Signed contracts expiring within `within_days` of today (default 30) or in `month` (YYYY-MM),
    with the last day to give notice."""
    async def body(col: Collector) -> dict:
        start, end = _window(within_days, month)
        async with tx(col) as conn:
            rows = await fetch_all(conn, "SELECT * FROM legal.contracts WHERE status = 'signed'"
                                         " AND expires_on BETWEEN %s AND %s ORDER BY expires_on, contract_id",
                                   (start, end))
        items = []
        for r in rows:
            c = col.row("legal.contract", r["contract_id"], r, ["contract_id", "account_id", "counterparty",
                                                                 "contract_type", "expires_on", "auto_renew",
                                                                 "notice_days", "handler_id"])
            if c is not None:
                c["notice_deadline"] = (r["expires_on"] - timedelta(days=int(r["notice_days"]))).isoformat()
                items.append(c)
        return col.envelope({"from": start.isoformat(), "to": end.isoformat(), "today": clock.today().isoformat(),
                             "contracts": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "contracts_expiring", body)


async def get_clause_library(ctx: Context, clause_type: str | None = None) -> dict:
    """Standard clause positions (preferred / fallback / unacceptable)."""
    async def body(col: Collector) -> dict:
        ct = clause_type or None
        if ct is not None and (not isinstance(ct, str) or not _CLAUSE_TYPE.match(ct)):
            raise ToolArgError("clause_type must be a short lower-case name like liability_cap")
        async with tx(col) as conn:
            rows = await fetch_all(conn, "SELECT * FROM legal.clause_library WHERE %s::text IS NULL OR clause_type = %s"
                                         " ORDER BY clause_id", (ct, ct))
        items = [c for r in rows if (c := col.row("legal.clause", r["clause_id"], r, CLAUSE_FIELDS))]
        return col.envelope({"clauses": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "get_clause_library", body)


async def search_regulations(ctx: Context, query: str = "", limit: int = 10) -> dict:
    """The GDPR / AI Act library (public summaries with article numbers)."""
    async def body(col: Collector) -> dict:
        q, lim = search_text(query), check_limit(limit, default=10, maximum=50)
        async with tx(col) as conn:
            rows = await fetch_all(conn,
                                   "SELECT * FROM legal.regulations WHERE %s = '' OR reg_id ILIKE '%%' || %s || '%%'"
                                   " OR title ILIKE '%%' || %s || '%%' OR summary ILIKE '%%' || %s || '%%'"
                                   " ORDER BY reg_id LIMIT %s", (q, q, q, q, lim))
        items = [x for r in rows if (x := col.row("legal.regulation", r["reg_id"], r, REG_FIELDS))]
        return col.envelope({"regulations": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "search_regulations", body)


# ---- write tools: proposals only ------------------------------------------------------------

async def create_contract_draft(ctx: Context, account_id: str, contract_type: str, text: str,
                                governing_law: str = "SE") -> dict:
    """Propose a new draft contract handled by you (tier 2)."""
    async def body(col: Collector) -> dict:
        params = {"account_id": check_id("account_id", account_id),
                  "contract_type": check_choice("contract_type", contract_type, CONTRACT_TYPES),
                  "text": check_text("text", text, max_len=100000),
                  "governing_law": check_choice("governing_law", governing_law,
                                                ("SE", "DE", "NL", "FR", "PL", "AT", "IT", "ES"))}
        p = col.auth.principal
        label = Label(level=Level.L3, dept=Dept.LEGAL,
                      acl_groups=frozenset({f"user:{p.user_id}", "head:legal", "role:ceo"}))  # §5.1 contract
        if not may_propose_new(col, "legal.contract", label):
            return refused(col)
        return await file_proposal(col, tool="create_contract_draft", params=params,
                                   summary=f"New {params['contract_type']} draft for account {params['account_id']}",
                                   level_floor=3)
    return await run_tool(ctx, DOMAIN, "create_contract_draft", body)


async def send_contract(ctx: Context, contract_id: str, to: str, subject: str, body: str = "") -> dict:
    """Propose sending a contract to the customer (tier 3: another person must approve).
    The recipient must be a CRM contact of the contract's account (checked at execution)."""
    async def run(col: Collector) -> dict:
        cid = check_id("contract_id", contract_id)
        params = {"contract_id": cid, "to": check_email("to", to),
                  "subject": check_text("subject", subject, max_len=300),
                  "body": check_text("body", body, max_len=20000, min_len=0) if body else ""}
        col.resource = f"legal.contract:{cid}"
        async with tx(col) as conn:
            row = await fetch_one(conn, "SELECT * FROM legal.contracts WHERE contract_id = %s", (cid,))
        if not may_propose_on(col, "legal.contract", cid, row):
            return refused(col)
        if row["status"] in ("expired", "terminated"):
            raise ToolArgError("an expired or terminated contract cannot be sent")
        return await file_proposal(col, tool="send_contract", params=params,
                                   summary=f"Send {cid} ({row['counterparty']}) to {params['to']}: {params['subject']}",
                                   level_floor=int(row["level"]))
    return await run_tool(ctx, DOMAIN, "send_contract", run)


TOOLS = [search_contracts, get_contract, contracts_expiring, get_clause_library, search_regulations,
         create_contract_draft, send_contract]
