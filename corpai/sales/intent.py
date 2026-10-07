"""Intent score from CRM, as the requesting user (RLS), in code — never in a model call.

The CRM rows are L3. They are read inside `principal_tx`, so a rep sees only accounts they own
(the head of sales and the CEO see all); an account the user cannot see is refused explicitly
instead of being scored from partial data.
"""

from __future__ import annotations

from datetime import date

from corpai import audit, clock
from corpai.db import pg
from corpai.policy import tool_allowed
from corpai.sales.errors import NoPermission
from corpai.sales.scoring import intent_from_rows
from corpai.types import Principal

AGENT = "sales_profile"
# The CRM tools whose data the score uses; a user must hold every one of them through the agent
# (legal staff with a one-account grant hold get_account only, so they get no intent score).
_TOOLS = ("get_account", "list_opportunities", "get_interactions")


def _check_tools(principal: Principal) -> None:
    for tool in _TOOLS:
        d = tool_allowed(principal, AGENT, "crm", tool)
        if not d.allow:
            raise NoPermission(f"no permission for CRM intent data ({d.rule_id})")


async def intent_score(principal: Principal, account_id: str, *, as_of: date | None = None,
                       run_id: str | None = None) -> tuple[int, list[str]]:
    """0-100 plus reason codes, from the account's interactions, open opportunities and quotes."""
    as_of = as_of or clock.today()
    try:
        _check_tools(principal)
        async with pg.principal_tx(principal, run_id=run_id) as conn:
            cur = await conn.execute("SELECT account_id FROM crm.accounts WHERE account_id = %s", (account_id,))
            if await cur.fetchone() is None:
                # Same answer for "hidden by RLS" and "does not exist": existence is not leaked.
                raise NoPermission("no permission for this account (or it does not exist)")
            cur = await conn.execute(
                "SELECT occurred_on, sentiment, kind FROM crm.interactions WHERE account_id = %s", (account_id,))
            interactions = await cur.fetchall()
            cur = await conn.execute("SELECT stage FROM crm.opportunities WHERE account_id = %s", (account_id,))
            opportunities = await cur.fetchall()
            cur = await conn.execute("SELECT valid_until FROM crm.quotes WHERE account_id = %s", (account_id,))
            quotes = await cur.fetchall()
    except NoPermission as e:
        await audit.arecord("policy_decision", actor=principal.user_id, act_chain=[f"agent:{AGENT}"],
                            action="sales.intent_score", resource=f"crm.account:{account_id}", decision="deny",
                            reason=str(e)[:200], run_id=run_id)
        raise
    return intent_from_rows(interactions, opportunities, quotes, as_of=as_of, use_pipeline=True)
