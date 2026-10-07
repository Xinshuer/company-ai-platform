"""Finance: chart of accounts, invoices (AR), a double-entry journal driven by invoices, payroll
and vendor bills, and monthly department budgets.

Everything is Decimal to the cent so planted figures recompute exactly in SQL. Salaries are posted
per department and month, never per person: the ledger is L3, individual pay is L4 (hr.compensation).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from corpai.synth import labels
from corpai.synth._util import (END, SIM_TODAY, START, World, days, label, money, month_end, months, period, pick,
                                rand_date, rng_for, split_amount)
from corpai.synth.script import OPENING_BANK_BALANCE_EUR, SERIES_B

CFO, FIN_STAFF, BILLING = "u0040", "u0041", "u0147"
OVERDUE_ACCOUNT = "A-0001"
OVERRUN_DEPT = "sales"
Q3 = ("2026-07", "2026-08", "2026-09")

CHART: list[tuple[str, str, str]] = [
    ("1200", "Accounts receivable", "asset"), ("1500", "Prepaid expenses", "asset"), ("1800", "Bank", "asset"),
    ("2000", "Share capital", "equity"), ("3300", "Accounts payable", "liability"),
    ("3700", "Accrued liabilities", "liability"), ("4000", "Subscription revenue", "revenue"),
    ("4100", "Professional services revenue", "revenue"), ("6000", "Salaries and wages", "expense"),
    ("6010", "Employer social security", "expense"), ("6100", "Rent and facilities", "expense"),
    ("6200", "Cloud hosting", "expense"), ("6210", "Software subscriptions", "expense"),
    ("6300", "Travel", "expense"), ("6400", "Marketing and events", "expense"), ("6500", "Recruiting", "expense"),
    ("6600", "Legal and advisory", "expense"), ("6700", "Training", "expense"),
    ("6800", "Office and equipment", "expense"), ("6900", "Audit and accounting", "expense"),
]

# Monthly run-rate of non-payroll costs per (dept, account), EUR. Payroll comes from hr.compensation.
_RUN_RATE: dict[tuple[str, str], int] = {
    ("eng", "6200"): 34_000, ("eng", "6210"): 6_000, ("sales", "6300"): 9_000, ("sales", "6400"): 14_000,
    ("hr", "6500"): 6_000, ("hr", "6700"): 3_000, ("legal", "6600"): 7_000, ("finance", "6900"): 4_000,
    ("it", "6210"): 9_000, ("it", "6800"): 5_000, ("exec", "6100"): 38_000, ("exec", "6300"): 3_000,
}
# Unbudgeted one-offs: EuroFleet Expo booth and travel (the planted Q3 overrun).
_SPIKES: dict[tuple[str, str, str], int] = {
    ("sales", "6400", "2026-09"): 95_000, ("sales", "6300", "2026-09"): 18_000, ("sales", "6300", "2026-08"): 10_000,
}
# Budgeted step changes: patent counsel for M-02 from July, more recruiting from June.
_STEPS: dict[tuple[str, str], tuple[str, int]] = {("legal", "6600"): ("2026-07", 12_000),
                                                   ("hr", "6500"): ("2026-06", 5_000)}
_VENDOR = {"6100": "Landlord Spreebogen Offices", "6200": "Cloud provider invoice", "6210": "SaaS licences",
           "6300": "Travel booking", "6400": "Events and campaigns", "6500": "Recruiting agency and job ads",
           "6600": "External counsel", "6700": "Training provider", "6800": "Office supplies and hardware",
           "6900": "Auditor and tax adviser"}


class _Journal:
    def __init__(self) -> None:
        self.txns: list[tuple[date, str, list[dict]]] = []

    def txn(self, on: date, sortkey: str, lines: list[tuple[str, str, Decimal, Decimal]], desc: str, *,
            status: str = "posted", project_id: str | None = None, account_id: str | None = None) -> None:
        assert sum(d for _, _, d, _ in lines) == sum(c for _, _, _, c in lines), "unbalanced transaction"
        rows = [{"posted_on": on, "period": period(on), "account_code": code, "dept": dept, "description": desc,
                 "debit_eur": money(d), "credit_eur": money(c), "project_id": project_id,
                 "customer_account_id": account_id, "status": status} for code, dept, d, c in lines]
        self.txns.append((on, sortkey, rows))


def build(world: World) -> None:
    rng = rng_for(world.seed, "fin")
    lvl, acl = labels.FIN
    for code, name, typ in CHART:
        world.add("fin.chart_of_accounts", {"account_code": code, "name": name, "type": typ,
                                            **label(2, "finance", CFO, acl)})
    j = _Journal()
    _funding(j)
    _invoices(world, rng, j)
    unbudgeted = _expenses(world, rng, j)
    j.txns.sort(key=lambda t: (t[0], t[1]))
    n = 1
    for t, (_, _, rows) in enumerate(j.txns, start=1):
        for r in rows:
            # The spec's business column `dept` (cost centre) doubles as the label's dept column.
            world.add("fin.journal_entries", {"entry_id": f"JE-{n:06d}", "txn_id": f"T-{t:05d}", **r,
                                              **label(lvl, r["dept"], FIN_STAFF, acl)})
            n += 1
    _budgets(world, rng, unbudgeted)


def _funding(j: _Journal) -> None:
    """Opening bank balance and the Series B (issues-log D-12): without them the bank account only
    holds the year's flows, and a cash balance would be the burn, a large negative number.

    The opening balance is posted on the day before the window (2025-09-30, period 2025-09): it is in
    every balance from 2025-10-01 on, but in no period's cash_net_flow. Posted on 2025-10-01 it would
    count as EUR 3.2M of cash received in October and turn that month's burn into an inflow."""
    opening = money(OPENING_BANK_BALANCE_EUR)
    before = START - days(1)
    j.txn(before, "0open", [("1800", "finance", opening, Decimal(0)), ("2000", "finance", Decimal(0), opening)],
          f"Opening balance bank {before.isoformat()} (carried forward into {period(START)})")
    on, amount, investor = SERIES_B
    cash = money(amount)
    j.txn(on, "0seriesb", [("1800", "finance", cash, Decimal(0)), ("2000", "finance", Decimal(0), cash)],
          f"Series B equity funding received ({investor})")


def _invoice_plan(world: World, rng) -> list[tuple[str, date, Decimal, str]]:
    """(account_id, issued_on, amount, kind) for subscriptions and services."""
    plan = []
    for a in world.rows("crm.accounts"):
        arr = a["arr_eur"]
        if a["status"] == "churned":
            arr, last = Decimal(61_000 if a["account_id"] == "A-0012" else 40_000), date(2026, 3, 31)
        else:
            last = END
        if arr <= 0:
            continue
        first = max(START, date(a["created_on"].year, a["created_on"].month, 1))
        if a["account_id"] == "A-0007":
            first = date(2026, 8, 1)
        monthly = a["tier"] in ("strategic", "key")
        for m in months(first, last):
            if monthly:
                plan.append((a["account_id"], m, money(arr / 12), "subscription"))
            elif m.month in (1, 4, 7, 10):
                plan.append((a["account_id"], m, money(arr / 4), "subscription"))
    customers = [a for a in world.rows("crm.accounts") if a["status"] == "customer"]
    for _ in range(8 if world.small else 130):
        a = pick(rng, customers)
        plan.append((a["account_id"], rand_date(rng, START, date(2026, 9, 25)), money(rng.randint(4, 30) * 1000),
                     "services"))
    plan.sort(key=lambda p: (p[1], p[0], p[3], p[2]))
    return plan


def _invoices(world: World, rng, j: _Journal) -> None:
    owner = {a["account_id"]: a for a in world.rows("crm.accounts")}
    lvl, acl = labels.FIN
    for n, (acc, issued, amount, kind) in enumerate(_invoice_plan(world, rng), start=1):
        inv_id = f"INV-{n:05d}"
        due = issued + days(30)
        if acc == OVERDUE_ACCOUNT and issued >= date(2026, 5, 1):
            paid_on = None  # withheld since the SLA dispute (matter M-01)
        elif due >= SIM_TODAY:
            paid_on = issued + days(rng.randint(5, 20)) if rng.random() < 0.3 else None
            paid_on = paid_on if paid_on and paid_on <= END else None
        else:
            late = rng.random() < 0.15
            paid_on = due + days(rng.randint(1, 25)) if late else issued + days(rng.randint(8, 30))
            # Non-planted debtors are at most ~45 days late, so the >60-day bucket belongs to A-0001 alone.
            if paid_on > END:
                paid_on = None if due >= date(2026, 8, 17) else END - days(rng.randint(0, 5))
        status = "paid" if paid_on else ("overdue" if due < SIM_TODAY else "open")
        world.add("fin.invoices", {"invoice_id": inv_id, "customer_account_id": acc, "issued_on": issued,
                                   "due_on": due, "amount_eur": amount, "status": status, "paid_on": paid_on,
                                   "kind": kind, **label(lvl, "finance", BILLING, acl)})
        rev = "4000" if kind == "subscription" else "4100"
        j.txn(issued, f"a{inv_id}", [("1200", "sales", amount, Decimal(0)), (rev, "sales", Decimal(0), amount)],
              f"Invoice {inv_id} {owner[acc]['name']}", account_id=acc)
        if paid_on:
            j.txn(paid_on, f"b{inv_id}", [("1800", "finance", amount, Decimal(0)),
                                          ("1200", "sales", Decimal(0), amount)],
                  f"Payment {inv_id}", account_id=acc)


def _expenses(world: World, rng, j: _Journal) -> dict[tuple[str, str], Decimal]:
    users = {u["user_id"]: u for u in world.rows("core.users")}
    left_on = {e["employee_id"]: e["left_on"] for e in world.rows("hr.employees")}
    projects = [p["project_id"] for p in world.rows("eng.projects")]
    unbudgeted: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for m in months():
        per, end = period(m), month_end(m)
        payroll: dict[str, Decimal] = defaultdict(Decimal)
        for c in world.rows("hr.compensation"):
            u = users[c["employee_id"]]
            left = left_on.get(c["employee_id"])
            # leavers are paid up to their last day, which is always a month end
            if u["hired_on"] <= end and (left is None or left >= end):
                payroll[u["dept"]] += c["base_salary_eur"] / 12
        for dept in sorted(payroll):
            gross = money(payroll[dept])
            social = money(gross * Decimal("0.21"))
            j.txn(end - days(2), f"p{dept}", [("6000", dept, gross, Decimal(0)), ("1800", dept, Decimal(0), gross)],
                  f"Payroll {per} {dept} (aggregate)")
            j.txn(end - days(2), f"s{dept}", [("6010", dept, social, Decimal(0)), ("1800", dept, Decimal(0), social)],
                  f"Employer social security {per} {dept}")
        for (dept, code), base in sorted(_RUN_RATE.items()):
            amount = Decimal(base) * Decimal(str(round(rng.uniform(0.93, 1.07), 3)))
            step = _STEPS.get((dept, code))
            if step and per >= step[0]:
                amount += step[1]
            spike = Decimal(_SPIKES.get((dept, code, per), 0))
            unbudgeted[(dept, per)] += spike
            total = money(amount + spike)
            parts = 1 if world.small else rng.randint(4, 8)
            for k, piece in enumerate(split_amount(rng, total, parts)):
                on = m + days(rng.randint(0, (end - m).days)) if parts > 1 else end
                proj = pick(rng, projects) if code == "6200" and parts > 1 and projects else None
                j.txn(on, f"v{dept}{code}{k}", [(code, dept, piece, Decimal(0)), ("1800", dept, Decimal(0), piece)],
                      f"{_VENDOR[code]} {per}", project_id=proj)
    # September close still running: accruals booked as drafts (excluded from the posted actuals).
    for k, (dept, code, amt) in enumerate([("finance", "6900", 3_500), ("legal", "6600", 2_800)]):
        a = money(amt)
        j.txn(date(2026, 9, 30), f"x{k}", [(code, dept, a, Decimal(0)), ("3700", dept, Decimal(0), a)],
              f"Accrual {_VENDOR[code]} 2026-09 (draft)", status="draft")
    return unbudgeted


def _budgets(world: World, rng, unbudgeted: dict[tuple[str, str], Decimal]) -> None:
    expense = {c for c, _, t in CHART if t == "expense"}
    actual: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for r in world.rows("fin.journal_entries"):
        if r["account_code"] in expense and r["status"] == "posted":
            actual[(r["dept"], r["period"])] += r["debit_eur"] - r["credit_eur"]
    lvl, acl = labels.FIN
    for dept, per in sorted(actual):
        planned = actual[(dept, per)] - unbudgeted[(dept, per)]
        # 0.98-1.10 keeps every other dept-quarter within ~2 % of budget, so the planted overrun is the only big one
        budget = (planned * Decimal(str(round(rng.uniform(0.98, 1.10), 3))) / 100).quantize(Decimal(1)) * 100
        world.add("fin.budgets", {"dept": dept, "period": per, "budget_eur": money(budget),
                                  **label(lvl, dept, CFO, acl)})


def quarter_actuals(world: World, dept: str, periods: tuple[str, ...]) -> tuple[Decimal, Decimal]:
    """(posted expense actuals, budget) for a dept over some periods; used for synth.planted."""
    expense = {c for c, _, t in CHART if t == "expense"}
    act = sum((r["debit_eur"] - r["credit_eur"] for r in world.rows("fin.journal_entries")
               if r["dept"] == dept and r["period"] in periods and r["account_code"] in expense
               and r["status"] == "posted"), Decimal(0))
    bud = sum((b["budget_eur"] for b in world.rows("fin.budgets") if b["dept"] == dept and b["period"] in periods),
              Decimal(0))
    return money(act), money(bud)
