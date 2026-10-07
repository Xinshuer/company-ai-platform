"""contracts_due: "which contracts expire next month" as SQL, not as a model reading documents.

Dates are relative to the simulation clock (clock.today()), never the OS date. The notice
deadline is the last day to act: expires_on - notice_days. For auto-renewing contracts that is
the last day to give notice of non-renewal; otherwise the last day to agree a renewal in time.
RLS decides which rows the caller sees (handling lawyer, head of legal, CEO).
"""

from __future__ import annotations

import re
from datetime import date

from corpai import clock
from corpai.db import pg
from corpai.types import Principal

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")

_SQL = """
SELECT contract_id, account_id, counterparty, contract_type, status, expires_on, notice_days, auto_renew,
       handler_id, level, (expires_on - notice_days) AS notice_deadline
FROM legal.contracts
WHERE status = 'signed' AND expires_on >= %s AND expires_on < %s
ORDER BY expires_on, contract_id
"""


def month_bounds(month: str | None) -> tuple[date, date]:
    """[first day, first day of the following month) for 'YYYY-MM'; None = the month after today."""
    if month is None:
        t = clock.today()
        y, m = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
    else:
        mm = _MONTH.fullmatch(month) if isinstance(month, str) else None
        if mm is None:
            raise ValueError("month must look like 2026-11")
        y, m = int(mm.group(1)), int(mm.group(2))
        if not 1 <= m <= 12 or not 2000 <= y <= 2100:
            raise ValueError("month must look like 2026-11")
    start = date(y, m, 1)
    end = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return start, end


async def contracts_due(principal: Principal, *, month: str | None = None, run_id: str | None = None) -> list[dict]:
    """Signed contracts expiring in `month` that the principal may read, with their notice deadline.

    Each row: contract_id, account_id, counterparty, contract_type, expires_on, notice_days, auto_renew,
    handler_id, level, notice_deadline, days_to_notice_deadline, notice_deadline_passed, action.
    """
    start, end = month_bounds(month)
    today = clock.today()
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute(_SQL, (start, end))
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        deadline: date = r["notice_deadline"]
        r["days_to_notice_deadline"] = (deadline - today).days
        r["notice_deadline_passed"] = deadline < today
        if r["auto_renew"]:
            r["action"] = f"renews automatically unless notice of non-renewal is given by {deadline.isoformat()}"
        else:
            r["action"] = f"ends on {r['expires_on'].isoformat()}; agree any renewal by {deadline.isoformat()}"
    return rows
