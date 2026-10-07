"""finance server: ledger, invoices, reconciliation and the M3 metric layer (architecture §11).

Ledger, invoices and budgets are L3 for finance and the CEO (§5.1). Numbers are summed here from
rows the caller may read, so a model never has to add them up.

list_metrics / get_metric read corpai.metrics as the caller: RLS on metrics.snapshots plus decide()
on every row's label (published company metrics L2 for everyone; drafts for finance). A metric the
caller may not see comes back as status "unavailable" with the reason, and is listed in `denied`.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.server.mcpserver import Context

from corpai.mcp_servers._common import (
    DEPTS,
    check_date,
    check_id,
    check_limit,
    check_period,
    check_text,
    fetch_all,
    opt_choice,
    opt_id,
    tx,
)
from corpai.mcp_servers._envelope import Collector, ToolArgError, run_tool
from corpai.mcp_servers._propose import file_proposal, may_propose_new, refused
from corpai.types import Dept, Label, Level

DOMAIN = "finance"
INVOICE_FIELDS = ["invoice_id", "customer_account_id", "issued_on", "due_on", "amount_eur", "status", "paid_on", "kind"]
FIN_LABEL = Label(level=Level.L3, dept=Dept.FINANCE, acl_groups=frozenset({"dept:finance", "head:finance",
                                                                          "role:ceo"}))  # §5.1 ledger
MAX_LINES = 50
MAX_AMOUNT = Decimal("1000000000")


MAX_DIMS = 4


def _metric_def(metric_id: object) -> Any:
    from corpai import metrics

    if not isinstance(metric_id, str) or len(metric_id) > 64:
        raise ToolArgError("metric_id must be a metric id from finance.list_metrics")
    try:
        return metrics.get_def(metric_id)
    except KeyError:
        raise ToolArgError("unknown metric_id; finance.list_metrics lists them") from None


def _dims(dims: object, allowed: Any) -> dict[str, str] | None:
    if dims is None or dims == {}:
        return None
    if not isinstance(dims, dict) or len(dims) > MAX_DIMS:
        raise ToolArgError(f"dims must be an object with at most {MAX_DIMS} entries")
    out = {}
    for k, v in dims.items():
        if k not in allowed or not isinstance(v, str):
            raise ToolArgError("dims names must be the metric's dimensions and values strings")
        out[k] = check_text("dims value", v, max_len=64)
    return out


async def list_metrics(ctx: Context) -> dict:
    """Company metrics: definitions, and the periods with values you may read."""
    async def body(col: Collector) -> dict:
        from corpai import metrics

        rows = await metrics.list_metrics(col.auth.principal)
        # Definitions are internal documentation (L2); values only come through get_metric.
        col.add_level(Level.L2, source="fin.metric_definitions")
        for r in rows:
            if r.get("access") != "visible":
                col.deny(f"fin.metric:{r['metric_id']}", r.get("access") or "no permission or not published")
        return col.envelope({"metrics": rows, "count": len(rows)})
    return await run_tool(ctx, DOMAIN, "list_metrics", body)


async def get_metric(ctx: Context, metric_id: str, period: str | None = None, dims: dict | None = None) -> dict:
    """One metric value with its provenance. Without a period: the newest period you may read."""
    async def body(col: Collector) -> dict:
        from corpai import metrics

        md = _metric_def(metric_id)
        dm = _dims(dims, set(md.dims))
        p = col.auth.principal
        if period is None:
            listed = next((r for r in await metrics.list_metrics(p) if r["metric_id"] == md.id), None)
            periods = [x["period"] for x in (listed or {}).get("periods", [])]
            per = max(periods, default=None)
        else:
            try:
                per = metrics.parse_period(period).text
            except ValueError:
                raise ToolArgError("period must look like 2026-09 or 2026-Q3") from None
        col.resource = f"fin.metric:{md.id}"
        if per is None:
            col.deny(f"fin.metric:{md.id}", metrics.NO_ACCESS)
            col.add_level(Level.L1, public=True)
            return col.envelope({"metric": {"metric_id": md.id, "status": "unavailable", "value": None,
                                            "reason": metrics.NO_ACCESS}})
        mv = await metrics.get_metric(p, md.id, per, dm)
        if not mv.available:
            col.deny(f"fin.metric:{md.id}:{per}", mv.reason or metrics.NO_ACCESS)
            col.add_level(Level.L1, public=True)
        else:
            # metrics.get_metric returned it only after RLS and decide() on the row's label
            col.add_level(int(mv.level), source=f"fin.metric:{md.id}:{per}", dept="finance")
        return col.envelope({"metric": mv.to_dict()})
    return await run_tool(ctx, DOMAIN, "get_metric", body)


async def query_ledger(ctx: Context, period: str | None = None, dept: str | None = None,
                       account_code: str | None = None, limit: int = 50) -> dict:
    """Ledger totals per account, period and cost centre (posted and draft lines kept apart)."""
    async def body(col: Collector) -> dict:
        per = check_period("period", period) if period else None
        dp = opt_choice("dept", dept, DEPTS)
        code = opt_id("account_code", account_code)
        lim = check_limit(limit, default=50, maximum=500)
        async with tx(col) as conn:
            rows = await fetch_all(conn,
                                   "SELECT * FROM fin.journal_entries WHERE (%s::text IS NULL OR period = %s)"
                                   " AND (%s::text IS NULL OR dept = %s) AND (%s::text IS NULL OR account_code = %s)"
                                   " ORDER BY posted_on DESC, entry_id DESC LIMIT 5000",
                                   (per, per, dp, dp, code, code))
        totals: dict[tuple, dict] = defaultdict(lambda: {"debit_eur": Decimal(0), "credit_eur": Decimal(0), "lines": 0})
        lines = []
        for r in rows:
            line = col.row("fin.ledger", r["entry_id"], r, ["entry_id", "txn_id", "posted_on", "period", "account_code",
                                                            "dept", "description", "debit_eur", "credit_eur", "status"])
            if line is None:
                continue
            t = totals[(r["period"], r["account_code"], r["dept"], r["status"])]
            t["debit_eur"] += r["debit_eur"]
            t["credit_eur"] += r["credit_eur"]
            t["lines"] += 1
            if len(lines) < lim:
                lines.append(line)
        out = [{"period": k[0], "account_code": k[1], "dept": k[2], "status": k[3], **v}
               for k, v in sorted(totals.items())]
        return col.envelope({"totals": out, "lines": lines, "line_count": sum(v["lines"] for v in totals.values())})
    return await run_tool(ctx, DOMAIN, "query_ledger", body)


async def list_invoices(ctx: Context, status: str | None = None, customer_account_id: str | None = None,
                        limit: int = 100) -> dict:
    """Invoices (receivables), optionally by status or customer."""
    async def body(col: Collector) -> dict:
        st = opt_choice("status", status, ("paid", "open", "overdue"))
        aid = opt_id("account_id", customer_account_id)
        lim = check_limit(limit, default=100, maximum=500)
        async with tx(col) as conn:
            rows = await fetch_all(conn,
                                   "SELECT * FROM fin.invoices WHERE (%s::text IS NULL OR status = %s)"
                                   " AND (%s::text IS NULL OR customer_account_id = %s) ORDER BY due_on, invoice_id"
                                   " LIMIT %s", (st, st, aid, aid, lim))
        items = [i for r in rows if (i := col.row("fin.invoice", r["invoice_id"], r, INVOICE_FIELDS))]
        total = sum((Decimal(str(i["amount_eur"])) for i in items), Decimal(0))
        return col.envelope({"invoices": items, "count": len(items), "total_eur": total})
    return await run_tool(ctx, DOMAIN, "list_invoices", body)


async def reconcile(ctx: Context, period: str) -> dict:
    """Checks for one period: unbalanced transactions, draft lines, invoices issued vs revenue posted."""
    async def body(col: Collector) -> dict:
        per = check_period("period", period)
        async with tx(col) as conn:
            lines = await fetch_all(conn, "SELECT j.*, c.type AS account_type FROM fin.journal_entries j"
                                          " LEFT JOIN fin.chart_of_accounts c USING (account_code) WHERE j.period = %s",
                                    (per,))
            invoices = await fetch_all(conn, "SELECT * FROM fin.invoices WHERE to_char(issued_on, 'YYYY-MM') = %s",
                                       (per,))
        by_txn: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        drafts, revenue = 0, Decimal(0)
        for r in lines:
            if col.row("fin.ledger", r["entry_id"], r, ["entry_id"]) is None:
                continue
            by_txn[r["txn_id"]] += r["debit_eur"] - r["credit_eur"]
            if r["status"] == "draft":
                drafts += 1
            elif r["account_type"] == "revenue":
                revenue += r["credit_eur"] - r["debit_eur"]
        issued = Decimal(0)
        for r in invoices:
            if col.row("fin.invoice", r["invoice_id"], r, ["invoice_id"]) is not None:
                issued += r["amount_eur"]
        return col.envelope({"period": per, "transactions": len(by_txn),
                             "unbalanced_txn_ids": sorted(t for t, d in by_txn.items() if d != 0),
                             "draft_lines": drafts, "invoices_issued_eur": issued, "revenue_posted_eur": revenue,
                             "difference_eur": issued - revenue})
    return await run_tool(ctx, DOMAIN, "reconcile", body)


# ---- write tool: proposal only ----------------------------------------------------------------

def _amount(name: str, v: Any) -> Decimal:
    try:
        d = Decimal(str(v)) if v not in (None, "") else Decimal(0)
    except (InvalidOperation, ValueError):
        raise ToolArgError(f"{name} must be an amount in EUR") from None
    if not d.is_finite() or d < 0 or d > MAX_AMOUNT or d != d.quantize(Decimal("0.01")):
        raise ToolArgError(f"{name} must be between 0 and 1e9 EUR with at most 2 decimals")
    return d.quantize(Decimal("0.01"))


def _lines(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_LINES:
        raise ToolArgError(f"lines must be a list of 2-{MAX_LINES} journal lines")
    out = []
    for line in raw:
        if not isinstance(line, dict):
            raise ToolArgError("each line must be an object")
        debit, credit = _amount("debit_eur", line.get("debit_eur")), _amount("credit_eur", line.get("credit_eur"))
        if (debit > 0) == (credit > 0):
            raise ToolArgError("each line needs exactly one of debit_eur or credit_eur")
        out.append({"account_code": check_id("account_code", line.get("account_code")),
                    "dept": opt_choice("dept", line.get("dept"), DEPTS) or "finance",
                    # amounts as strings: the hashed params must not depend on float formatting
                    "debit_eur": str(debit), "credit_eur": str(credit)})
    if sum(Decimal(l["debit_eur"]) for l in out) != sum(Decimal(l["credit_eur"]) for l in out):
        raise ToolArgError("the entry does not balance: total debit must equal total credit")
    return out


async def post_journal_entry(ctx: Context, posted_on: str, description: str, lines: list[dict]) -> dict:
    """Propose posting a balanced journal entry (tier 3: another person must approve)."""
    async def body(col: Collector) -> dict:
        params = {"posted_on": check_date("posted_on", posted_on).isoformat(),
                  "description": check_text("description", description, max_len=500), "lines": _lines(lines)}
        codes = sorted({l["account_code"] for l in params["lines"]})
        async with tx(col) as conn:
            known = await fetch_all(conn, "SELECT * FROM fin.chart_of_accounts WHERE account_code = ANY(%s)", (codes,))
        if len(known) != len(codes):
            raise ToolArgError("an account code is not in the chart of accounts you can see")
        if not may_propose_new(col, "fin.ledger", FIN_LABEL):
            return refused(col)
        total = sum(Decimal(l["debit_eur"]) for l in params["lines"])
        return await file_proposal(col, tool="post_journal_entry", params=params,
                                   summary=f"Post journal entry on {params['posted_on']}: {params['description']} "
                                           f"({len(params['lines'])} lines, EUR {total})", level_floor=3)
    return await run_tool(ctx, DOMAIN, "post_journal_entry", body)


TOOLS = [list_metrics, get_metric, query_ledger, list_invoices, reconcile, post_journal_entry]
