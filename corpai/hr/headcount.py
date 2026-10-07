"""Headcount summary (design-review 三 HR: 编制需求按字段拆开; decision D7; architecture §6 hr.aggregate).

Two parts with two levels, so the caller can keep the L2 part on the external route:
- `public` (L2): open roles and headcount per department from hr.requisitions, plus staff counts
  per department (person-derived: departments under 5 people are suppressed);
- `budget` (L3): approved and projected requisition budgets, only for rows the caller may read.
Everything runs as the caller (RLS); staff counts go through hr.headcount_by_dept(), which applies
the hr.aggregate rules and the n<5 suppression in SQL. Denials are listed, never skipped.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from corpai import clock
from corpai.db import pg
from corpai.hr.errors import NoPermission
from corpai.policy import decide, may_start
from corpai.types import Action, Dept, Label, Level, Principal, Resource

AGENT = "hr_headcount"
MIN_GROUP = 5


def _f(x: Decimal | None) -> float | None:
    return None if x is None else float(x)


def _aggregate_allowed(p: Principal, dept: str) -> bool:
    r = Resource(kind="hr.aggregate", id=f"headcount:{dept}", label=Label(level=Level.L2, dept=Dept.HR),
                 attrs={"dept": dept, "topic": "headcount"})
    return decide(p, Action.READ, r).allow


async def headcount_summary(principal: Principal, *, run_id: str | None = None) -> dict[str, Any]:
    started = may_start(principal, AGENT)
    if not started.allow:
        raise NoPermission(f"no permission for the headcount summary: {started.reason}")

    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute(
            "SELECT req_id, dept, title, headcount, status FROM hr.requisitions ORDER BY dept, req_id")
        reqs = await cur.fetchall()
        cur = await conn.execute(
            "SELECT req_id, approved_budget_eur, projected_cost_eur FROM hr.requisition_budgets ORDER BY req_id")
        budgets = {b["req_id"]: b for b in await cur.fetchall()}
        # people employed on the simulation date (leavers excluded), as in the headcount metric
        cur = await conn.execute("SELECT dept, employees, suppressed FROM hr.headcount_by_dept(%s)", (clock.today(),))
        staff_rows = await cur.fetchall()

    by_dept: dict[str, list[dict]] = defaultdict(list)
    for r in reqs:
        by_dept[r["dept"]].append({"req_id": r["req_id"], "title": r["title"], "headcount": r["headcount"],
                                   "status": r["status"]})
    requisitions = [{"dept": d, "open_headcount": sum(x["headcount"] for x in rows if x["status"] == "open"),
                     "roles": rows} for d, rows in sorted(by_dept.items())]

    # Staff counts: the policy decision and the SQL function must agree; a department the policy
    # denies is reported as denied even if SQL returned it (defence in depth, fail closed).
    staff, staff_denied = [], []
    returned = {s["dept"]: s for s in staff_rows}
    for d in sorted({str(x) for x in Dept}):
        if not _aggregate_allowed(principal, d):
            staff_denied.append({"dept": d, "reason": "no permission"})
            continue
        s = returned.get(d)
        if s is None:
            continue  # no employees in that department
        staff.append({"dept": d, "employees": s["employees"] if not s["suppressed"] else None,
                      "suppressed": bool(s["suppressed"] or (s["employees"] or 0) < MIN_GROUP)})

    # Budgets the caller may read, even where the requisition row itself is not visible (the CFO
    # holds head:finance on every budget, but sees only finance's requisitions); requisitions the
    # caller sees without their budget are listed as denied.
    req_dept = {r["req_id"]: r["dept"] for r in reqs}
    req_status = {r["req_id"]: r["status"] for r in reqs}
    items = []
    for req_id, b in sorted(budgets.items()):
        approved, projected = _f(b["approved_budget_eur"]), _f(b["projected_cost_eur"])
        status = req_status.get(req_id)  # None: the requisition row is not visible to this caller
        items.append({"req_id": req_id, "dept": req_dept.get(req_id), "status": status,
                      "approved_budget_eur": approved, "projected_cost_eur": projected,
                      "variance_pct": round((projected / approved - 1) * 100, 2) if approved else None,
                      # a current hiring issue only while the requisition is still being hired for,
                      # as in the hiring_* metrics and the hiring_over_budget signal
                      "over_budget": bool(approved and projected > approved
                                          and status in (None, "open", "on_hold"))})
    denied = [{"req_id": r["req_id"], "reason": "no permission"} for r in reqs if r["req_id"] not in budgets]

    return {
        "public": {"level": int(Level.L2), "requisitions_by_dept": requisitions, "staff_by_dept": staff,
                   "staff_denied": staff_denied, "min_group": MIN_GROUP},
        "budget": {"level": int(Level.L3), "allowed": bool(items), "items": items, "denied": denied},
        # The level of the whole summary: L3 as soon as any budget row is included.
        "level": int(Level.L3) if items else int(Level.L2),
    }
