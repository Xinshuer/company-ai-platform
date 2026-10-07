"""Issue detectors for the management report (design-review 七 M3: "the planted issues are found").

Each detector is plain SQL run with the requester's identity (pg.principal_tx, RLS applies), so a CEO
and a CTO get different evidence. When the requester can see no row of a detector's main table, the
result says "no_permission" instead of an empty, reassuring "nothing found" (ENGINEERING.md rule 6).
Detectors never read the synth schema (the evaluation answers) and return aggregates and business ids only:
no employee names (D7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable

import psycopg

from corpai.clock import today
from corpai.db import pg
from corpai.metrics.definitions import PEOPLE_MIN_GROUP as MIN_GROUP
from corpai.metrics.periods import parse_period, quarter_before
from corpai.types import Level, Principal

KINDS = ("overdue_receivable", "contract_expiring", "project_testing_delayed", "hiring_over_budget",
         "dept_budget_overrun", "missing_dpa_clause")
_NO_PERMISSION = "no permission"


@dataclass(frozen=True)
class SignalResult:
    kind: str
    status: str                          # "ok" | "no_permission"
    as_of: str
    findings: tuple[dict, ...] = ()
    level: Level = Level.L1              # max level of the rows behind the findings (feeds the graph's hwm)
    sources: tuple[str, ...] = ()        # "fin.invoice:INV-00042" refs behind the findings
    denied_sources: tuple[str, ...] = ()  # tables the requester can see no row of
    message: str = ""

    @property
    def found(self) -> bool:
        return self.status == "ok" and bool(self.findings)

    @property
    def refs(self) -> list[str]:
        return [f["ref"] for f in self.findings]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "status": self.status, "as_of": self.as_of, "findings": list(self.findings),
                "level": int(self.level), "sources": list(self.sources),
                "denied_sources": list(self.denied_sources), "message": self.message}


def _f(v: Any) -> Any:
    """JSON-friendly: Decimal → float (rounded to cents), date → ISO string."""
    if isinstance(v, Decimal):
        return float(round(v, 2))
    if isinstance(v, date):
        return v.isoformat()
    return v


async def _visible(conn: psycopg.AsyncConnection, table: str) -> bool:
    # `table` is one of the constants below, never user input.
    cur = await conn.execute(f"SELECT EXISTS (SELECT 1 FROM {table}) AS v")
    return bool((await cur.fetchone())["v"])


async def _denied(conn: psycopg.AsyncConnection, tables: tuple[str, ...]) -> tuple[str, ...]:
    return tuple([t for t in tables if not await _visible(conn, t)])


def _no_permission(kind: str, as_of: date, denied: tuple[str, ...]) -> SignalResult:
    return SignalResult(kind=kind, status="no_permission", as_of=as_of.isoformat(), denied_sources=denied,
                        message=f"{_NO_PERMISSION}: cannot read {', '.join(denied)}")


def _level(levels: list[int]) -> Level:
    # No matching row still means "the confidential source said nothing is wrong", which is L3 knowledge.
    return Level(max(levels)) if levels else Level.L3


def _next_month(as_of: date) -> tuple[date, date]:
    first = date(as_of.year + (as_of.month == 12), as_of.month % 12 + 1, 1)
    after = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first, after


# ---- detectors ------------------------------------------------------------------------------------

async def _overdue_receivable(conn: psycopg.AsyncConnection, as_of: date, *, min_days: int = 60) -> SignalResult:
    kind = "overdue_receivable"
    denied = await _denied(conn, ("fin.invoices", "crm.accounts"))
    if "fin.invoices" in denied:
        return _no_permission(kind, as_of, ("fin.invoices",))
    cur = await conn.execute(
        "SELECT invoice_id, customer_account_id, due_on, amount_eur, level, (%s::date - due_on) AS days_overdue "
        "FROM fin.invoices WHERE issued_on < %s AND due_on < %s AND (paid_on IS NULL OR paid_on >= %s) "
        "ORDER BY customer_account_id, due_on", (as_of, as_of, as_of, as_of))
    rows = await cur.fetchall()
    by_acc: dict[str, list[dict]] = {}
    for r in rows:
        by_acc.setdefault(r["customer_account_id"], []).append(r)
    flagged = {a: inv for a, inv in by_acc.items() if any(i["days_overdue"] > min_days for i in inv)}
    accounts: dict[str, dict] = {}
    if flagged and "crm.accounts" not in denied:
        cur = await conn.execute("SELECT account_id, name, tier, arr_eur, level FROM crm.accounts "
                                 "WHERE account_id = ANY (%s)", (list(flagged),))
        accounts = {r["account_id"]: r for r in await cur.fetchall()}
    findings, levels, sources = [], [], []
    for acc, inv in sorted(flagged.items()):
        over = [i for i in inv if i["days_overdue"] > min_days]
        a = accounts.get(acc)
        levels += [i["level"] for i in inv] + ([a["level"]] if a else [])
        sources += [f"fin.invoice:{i['invoice_id']}" for i in inv] + ([f"crm.account:{acc}"] if a else [])
        findings.append({
            "ref": acc, "account_id": acc, "account_name": a["name"] if a else None,
            "tier": a["tier"] if a else None, "arr_eur": _f(a["arr_eur"]) if a else None,
            "crm_visible": a is not None,
            "total_overdue_eur": _f(sum((i["amount_eur"] for i in inv), Decimal(0))),
            "invoices_over_threshold": [i["invoice_id"] for i in over],
            "amount_over_threshold_eur": _f(sum((i["amount_eur"] for i in over), Decimal(0))),
            "threshold_days": min_days, "max_days_overdue": max(i["days_overdue"] for i in inv)})
    return SignalResult(kind=kind, status="ok", as_of=as_of.isoformat(), findings=tuple(findings),
                        level=_level(levels), sources=tuple(sources), denied_sources=denied,
                        message=f"{len(findings)} customer(s) with invoices more than {min_days} days overdue")


async def _contract_expiring(conn: psycopg.AsyncConnection, as_of: date) -> SignalResult:
    kind = "contract_expiring"
    denied = await _denied(conn, ("legal.contracts",))
    if denied:
        return _no_permission(kind, as_of, denied)
    start, end = _next_month(as_of)
    cur = await conn.execute(
        "SELECT contract_id, account_id, counterparty, contract_type, expires_on, auto_renew, notice_days, level "
        "FROM legal.contracts WHERE status = 'signed' AND expires_on >= %s AND expires_on < %s "
        "ORDER BY expires_on, contract_id", (start, end))
    rows = await cur.fetchall()
    findings = []
    for r in rows:
        deadline = r["expires_on"] - timedelta(days=int(r["notice_days"]))
        findings.append({
            "ref": r["contract_id"], "contract_id": r["contract_id"], "account_id": r["account_id"],
            "counterparty": r["counterparty"], "contract_type": r["contract_type"],
            "expires_on": _f(r["expires_on"]), "auto_renew": r["auto_renew"], "notice_days": r["notice_days"],
            "notice_deadline": _f(deadline), "notice_deadline_passed": deadline < as_of,
            "days_to_notice_deadline": (deadline - as_of).days})
    return SignalResult(kind=kind, status="ok", as_of=as_of.isoformat(), findings=tuple(findings),
                        level=_level([r["level"] for r in rows]),
                        sources=tuple(f"legal.contract:{r['contract_id']}" for r in rows),
                        message=f"{len(findings)} signed contract(s) expire between {start} and {end - timedelta(days=1)}")


async def _project_testing_delayed(conn: psycopg.AsyncConnection, as_of: date, *,
                                   min_days: int = 45) -> SignalResult:
    kind = "project_testing_delayed"
    denied = await _denied(conn, ("eng.projects", "eng.ci_runs", "eng.tickets"))
    if "eng.projects" in denied:
        return _no_permission(kind, as_of, ("eng.projects",))
    cur = await conn.execute(
        "SELECT project_id, name, stage, stage_since, health, planned_go_live, level FROM eng.projects "
        "WHERE stage = 'testing' AND (%s::date - stage_since) > %s ORDER BY project_id", (as_of, min_days))
    projects = await cur.fetchall()
    findings, levels, sources = [], [], []
    for p in projects:
        pid = p["project_id"]
        levels.append(p["level"])
        sources.append(f"eng.project:{pid}")
        f: dict[str, Any] = {"ref": pid, "project_id": pid, "name": p["name"], "stage": p["stage"],
                             "stage_since": _f(p["stage_since"]), "days_in_stage": (as_of - p["stage_since"]).days,
                             "health": p["health"], "threshold_days": min_days}
        cur = await conn.execute(
            "SELECT run_id, status, started_at, level FROM eng.ci_runs WHERE project_id = %s "
            "AND (started_at AT TIME ZONE 'UTC')::date < %s ORDER BY started_at", (pid, as_of))
        runs = await cur.fetchall()
        if runs:
            streak = 0
            for r in reversed(runs):
                if r["status"] != "failed":
                    break
                streak += 1
            levels += [r["level"] for r in runs[-max(streak, 5):]]
            sources += [f"eng.ci_run:{r['run_id']}" for r in runs[-5:]]
            f.update(ci_visible=True, trailing_failed_runs=streak, last_5_run_ids=[r["run_id"] for r in runs[-5:]],
                     ci_red=streak >= 3)
        else:
            f.update(ci_visible=False, trailing_failed_runs=None, last_5_run_ids=[], ci_red=None)
        cur = await conn.execute(
            "SELECT ticket_id, title, status, level FROM eng.tickets WHERE project_id = %s "
            "AND title ILIKE %s ORDER BY ticket_id", (pid, "%testing complete%"))
        claims = await cur.fetchall()
        levels += [t["level"] for t in claims]
        sources += [f"eng.ticket:{t['ticket_id']}" for t in claims]
        f["claim_ticket_ids"] = [t["ticket_id"] for t in claims]
        # The inconsistency the design review asks for: a ticket says testing is done while CI is red.
        f["inconsistent"] = bool(claims) and bool(f.get("ci_red"))
        findings.append(f)
    return SignalResult(kind=kind, status="ok", as_of=as_of.isoformat(), findings=tuple(findings),
                        level=_level(levels), sources=tuple(sources), denied_sources=denied,
                        message=f"{len(findings)} project(s) in testing for more than {min_days} days")


async def _hiring_over_budget(conn: psycopg.AsyncConnection, as_of: date) -> SignalResult:
    kind = "hiring_over_budget"
    denied = await _denied(conn, ("hr.requisition_budgets", "hr.requisitions"))
    if "hr.requisition_budgets" in denied:
        return _no_permission(kind, as_of, ("hr.requisition_budgets",))
    # Only requisitions still being hired for (open / on hold) on the as_of date, like the hiring_*
    # metrics at a period's end: a filled or closed requisition is history, not a current hiring
    # issue. The status comes from the status dates (issues-log D-12), so an earlier as_of sees the
    # status of that day. When the requisition row is hidden from this reader (RLS), its status is
    # unknown: it is kept, and says so, rather than dropped.
    cur = await conn.execute(
        "SELECT b.req_id, b.approved_budget_eur, b.projected_cost_eur, b.level, r.dept, r.title, "
        "CASE WHEN r.req_id IS NULL THEN NULL WHEN r.filled_on < %(d)s THEN 'filled' "
        "     WHEN r.closed_on < %(d)s THEN 'closed' WHEN r.on_hold_on < %(d)s THEN 'on_hold' ELSE 'open' END AS status, "
        "r.level AS req_level, (r.req_id IS NOT NULL) AS req_visible "
        "FROM hr.requisition_budgets b LEFT JOIN hr.requisitions r ON r.req_id = b.req_id "
        "WHERE b.projected_cost_eur > b.approved_budget_eur "
        "AND (r.req_id IS NULL OR (r.opened_on < %(d)s AND NOT coalesce(r.filled_on < %(d)s, false) "
        "                          AND NOT coalesce(r.closed_on < %(d)s, false))) ORDER BY b.req_id", {"d": as_of})
    rows = await cur.fetchall()
    findings = [{
        "ref": r["req_id"], "req_id": r["req_id"], "dept": r["dept"], "title": r["title"], "status": r["status"],
        "status_visible": bool(r["req_visible"]),
        "approved_budget_eur": _f(r["approved_budget_eur"]), "projected_cost_eur": _f(r["projected_cost_eur"]),
        "over_pct": float(round((r["projected_cost_eur"] / r["approved_budget_eur"] - 1) * 100, 2))}
        for r in rows]
    levels = [r["level"] for r in rows] + [r["req_level"] for r in rows if r["req_level"] is not None]
    return SignalResult(kind=kind, status="ok", as_of=as_of.isoformat(), findings=tuple(findings),
                        level=_level(levels), sources=tuple(f"hr.requisition_budget:{r['req_id']}" for r in rows),
                        denied_sources=denied,
                        message=f"{len(findings)} requisition(s) projected above the approved budget")


async def _dept_budget_overrun(conn: psycopg.AsyncConnection, as_of: date, *,
                               threshold_pct: float = 10.0, period: str | None = None) -> SignalResult:
    kind = "dept_budget_overrun"
    denied = await _denied(conn, ("fin.journal_entries", "fin.budgets"))
    if denied:
        return _no_permission(kind, as_of, denied)
    q = parse_period(period) if period else quarter_before(as_of)
    cur = await conn.execute(
        "WITH act AS (SELECT j.dept, sum(j.debit_eur - j.credit_eur) AS actual, max(j.level) AS lvl "
        "  FROM fin.journal_entries j JOIN fin.chart_of_accounts a ON a.account_code = j.account_code "
        "  WHERE a.type = 'expense' AND j.status = 'posted' AND j.period = ANY (%s) GROUP BY j.dept), "
        "bud AS (SELECT dept, sum(budget_eur) AS budget, max(level) AS lvl FROM fin.budgets "
        "  WHERE period = ANY (%s) GROUP BY dept) "
        "SELECT b.dept, coalesce(a.actual, 0) AS actual, b.budget, greatest(a.lvl, b.lvl) AS lvl "
        "FROM bud b LEFT JOIN act a ON a.dept = b.dept ORDER BY b.dept", (q.months, q.months))
    rows = await cur.fetchall()
    # D7, as in the opex metrics (min_group 5): a dept's opex includes its aggregate payroll, so for a
    # dept under five people no amount or percentage may leave this function. Which depts are small
    # comes from a SECURITY DEFINER function that returns only that boolean (most readers cannot
    # count hr.employees); a dept it does not report is treated as small (fail closed).
    cur = await conn.execute("SELECT dept, below FROM metrics.dept_below_min_group(%s)", (q.end,))
    sizes = {r["dept"]: bool(r["below"]) for r in await cur.fetchall()}
    findings, levels = [], []
    for r in rows:
        if r["budget"] <= 0:
            continue
        pct = (r["actual"] / r["budget"] - 1) * 100
        if pct >= Decimal(str(threshold_pct)):
            levels.append(r["lvl"])
            if sizes.get(r["dept"], True):
                findings.append({"ref": r["dept"], "dept": r["dept"], "period": q.text, "periods": q.months,
                                 "suppressed": True, "min_group": MIN_GROUP, "threshold_pct": threshold_pct})
                continue
            findings.append({"ref": r["dept"], "dept": r["dept"], "period": q.text, "periods": q.months,
                             "actual_eur": _f(r["actual"]), "budget_eur": _f(r["budget"]),
                             "overrun_pct": float(round(pct, 2)), "threshold_pct": threshold_pct})
    return SignalResult(kind=kind, status="ok", as_of=as_of.isoformat(), findings=tuple(findings),
                        level=_level(levels), sources=tuple(f"fin.budget:{f['dept']}:{q.text}" for f in findings),
                        message=f"{len(findings)} dept(s) at least {threshold_pct:g}% over budget in {q.text}")


async def _missing_dpa_clause(conn: psycopg.AsyncConnection, as_of: date) -> SignalResult:
    kind = "missing_dpa_clause"
    denied = await _denied(conn, ("legal.contracts",))
    if denied:
        return _no_permission(kind, as_of, denied)
    cur = await conn.execute(
        "SELECT c.contract_id, c.account_id, c.counterparty, c.contract_type, c.level FROM legal.contracts c "
        "WHERE c.status = 'signed' AND c.processes_personal_data AND NOT c.has_dpa_clause "
        "AND NOT EXISTS (SELECT 1 FROM legal.contracts d WHERE d.account_id = c.account_id "
        "                AND d.contract_type = 'DPA' AND d.status = 'signed') ORDER BY c.contract_id")
    rows = await cur.fetchall()
    findings = [{"ref": r["contract_id"], "contract_id": r["contract_id"], "account_id": r["account_id"],
                 "counterparty": r["counterparty"], "contract_type": r["contract_type"], "status": "signed",
                 "processes_personal_data": True, "has_dpa_clause": False,
                 # Under RLS only the DPAs this reader can see were checked; a lawyer may get a false alarm.
                 "separate_dpa": False, "checked_visible_contracts_only": True} for r in rows]
    return SignalResult(kind=kind, status="ok", as_of=as_of.isoformat(), findings=tuple(findings),
                        level=_level([r["level"] for r in rows]),
                        sources=tuple(f"legal.contract:{r['contract_id']}" for r in rows),
                        message=f"{len(findings)} signed contract(s) process personal data without Art. 28 terms")


_DETECTORS: dict[str, Callable[..., Awaitable[SignalResult]]] = {
    "overdue_receivable": _overdue_receivable, "contract_expiring": _contract_expiring,
    "project_testing_delayed": _project_testing_delayed, "hiring_over_budget": _hiring_over_budget,
    "dept_budget_overrun": _dept_budget_overrun, "missing_dpa_clause": _missing_dpa_clause,
}


async def detect(principal: Principal, kind: str, *, as_of: date | None = None, run_id: str | None = None,
                 **options: Any) -> SignalResult:
    """Run one detector as the principal. `options` are the detector's thresholds (min_days, threshold_pct,
    period); they come from code, never from a model."""
    if kind not in _DETECTORS:
        raise ValueError(f"unknown signal {kind!r}; known: {', '.join(KINDS)}")
    as_of = as_of or today()
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        return await _DETECTORS[kind](conn, as_of, **options)


async def detect_all(principal: Principal, *, as_of: date | None = None, run_id: str | None = None,
                     kinds: tuple[str, ...] = KINDS) -> dict[str, SignalResult]:
    """Every detector in one transaction (one consistent view of the data) as the principal."""
    as_of = as_of or today()
    for k in kinds:
        if k not in _DETECTORS:
            raise ValueError(f"unknown signal {k!r}")
    out: dict[str, SignalResult] = {}
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        for k in kinds:
            out[k] = await _DETECTORS[k](conn, as_of)
    return out


# Convenience wrappers with the names the report graph uses.
async def overdue_receivable(principal: Principal, **kw: Any) -> SignalResult:
    return await detect(principal, "overdue_receivable", **kw)


async def contract_expiring(principal: Principal, **kw: Any) -> SignalResult:
    return await detect(principal, "contract_expiring", **kw)


async def project_testing_delayed(principal: Principal, **kw: Any) -> SignalResult:
    return await detect(principal, "project_testing_delayed", **kw)


async def hiring_over_budget(principal: Principal, **kw: Any) -> SignalResult:
    return await detect(principal, "hiring_over_budget", **kw)


async def dept_budget_overrun(principal: Principal, **kw: Any) -> SignalResult:
    return await detect(principal, "dept_budget_overrun", **kw)


async def missing_dpa_clause(principal: Principal, **kw: Any) -> SignalResult:
    return await detect(principal, "missing_dpa_clause", **kw)
