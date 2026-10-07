"""hris server: employees, headcount, requisitions, candidates (architecture §11, decisions D6/D7).

Employee profiles are L3 for HR and the direct manager; pay and ratings are never served here.
Headcount is an aggregate (policy kind hr.aggregate): the CEO sees every department, other heads
their own, and groups under 5 people are shown as "<5" (obligation min_group_5, D7). HR staff read
every employee row anyway (acl dept:hr), so for them the count is simply the rows they can read.
Candidates and résumés are untrusted input (trust=untrusted travels in the label).
"""

from __future__ import annotations

from typing import Any

import psycopg
from mcp.server.mcpserver import Context

from corpai.mcp_servers._common import (
    DEPTS,
    check_choice,
    check_id,
    check_text,
    fetch_all,
    fetch_one,
    opt_choice,
    tx,
)
from corpai.mcp_servers._envelope import NOT_VISIBLE, Collector, ToolArgError, run_tool
from corpai.mcp_servers._propose import file_proposal, may_propose_new, refused
from corpai.policy import decide
from corpai.types import Action, Dept, Label, Level, Resource

DOMAIN = "hris"
EMPLOYEE_FIELDS = ["employee_id", "name", "dept", "title", "manager_id", "country", "hired_on", "employment_type", "fte"]
REQ_FIELDS = ["req_id", "dept", "title", "headcount", "status", "opened_on", "hiring_manager_id", "recruiter_id"]
BUDGET_FIELDS = ["approved_budget_eur", "projected_cost_eur", "comp_band_min", "comp_band_max"]
CANDIDATE_LIST_FIELDS = ["candidate_id", "req_id", "name", "stage", "applied_on", "source"]
CANDIDATE_FIELDS = CANDIDATE_LIST_FIELDS + ["email", "phone", "resume_text"]
MIN_GROUP = 5
SMALL_GROUP = "<5"


async def get_employee(ctx: Context, employee_id: str) -> dict:
    """An employee profile (no pay, no ratings): HR, or the employee's direct manager."""
    async def body(col: Collector) -> dict:
        eid = check_id("employee_id", employee_id)
        col.resource = f"hr.employee:{eid}"
        async with tx(col) as conn:
            row = await fetch_one(conn, "SELECT * FROM hr.employees WHERE employee_id = %s", (eid,))
        emp = col.row("hr.employee", eid, row, EMPLOYEE_FIELDS) if row else None
        if emp is None:
            col.deny(f"hr.employee:{eid}", NOT_VISIBLE)
            return col.error("not_found_or_denied", "no such employee, or you have no permission to see them")
        return col.envelope({"employee": emp})
    return await run_tool(ctx, DOMAIN, "get_employee", body)


def _aggregate(dept: str) -> Resource:
    return Resource(kind="hr.aggregate", id=f"headcount:{dept}", label=Label(level=Level.L2),
                    attrs={"dept": dept, "topic": "headcount"})


async def headcount(ctx: Context) -> dict:
    """People per department. Groups under 5 are shown as "<5" unless you can read every person in them."""
    async def body(col: Collector) -> dict:
        p = col.auth.principal
        hr_member = "dept:hr" in p.groups  # §5.1: HR reads every hr.employees row
        decisions = {d: decide(p, Action.READ, _aggregate(d)) for d in DEPTS}
        agg: dict[str, int] = {}
        async with tx(col) as conn:
            visible = {r["dept"]: int(r["n"]) for r in await fetch_all(
                conn, "SELECT dept, count(*) AS n FROM hr.employees GROUP BY dept")}
        if not hr_member and any(d.allow for d in decisions.values()):
            try:
                async with tx(col) as conn:
                    agg = {r["dept"]: int(r["n"]) for r in await fetch_all(conn,
                                                                         "SELECT * FROM ops.hr_headcount()")}
            except psycopg.errors.InsufficientPrivilege:
                agg = {}  # SQL disagrees with policy: show nothing rather than guess
        rows: list[dict[str, Any]] = []
        for d in DEPTS:
            dec = decisions[d]
            if hr_member:
                rows.append({"dept": d, "headcount": visible.get(d, 0)})
            elif dec.allow and d in agg:
                n = agg[d]
                small = "min_group_5" in dec.obligations and 0 < n < MIN_GROUP
                rows.append({"dept": d, "headcount": SMALL_GROUP if small else n})
                col.obligations |= set(dec.obligations)
            elif dec.allow:
                continue  # an empty department
            else:
                col.deny(f"hr.aggregate:headcount:{d}", dec.reason)
        col.add_level(Level.L2, source="hr.aggregate:headcount", dept=str(Dept.HR))
        return col.envelope({"departments": rows, "min_group": MIN_GROUP})
    return await run_tool(ctx, DOMAIN, "headcount", body)


async def list_requisitions(ctx: Context, status: str | None = None) -> dict:
    """Open roles and headcount (L2); the budget only where you may see it (L3)."""
    async def body(col: Collector) -> dict:
        st = opt_choice("status", status, ("open", "on_hold", "filled", "closed"))
        async with tx(col) as conn:
            reqs = await fetch_all(conn, "SELECT * FROM hr.requisitions WHERE %s::text IS NULL OR status = %s"
                                         " ORDER BY req_id", (st, st))
            budgets = {r["req_id"]: r for r in await fetch_all(conn, "SELECT * FROM hr.requisition_budgets")}
        items = []
        for r in reqs:
            item = col.row("hr.requisition", r["req_id"], r, REQ_FIELDS)
            if item is None:
                continue
            b = budgets.get(r["req_id"])
            if b is not None:
                item["budget"] = col.row("hr.requisition_budget", r["req_id"], b, BUDGET_FIELDS)
            else:
                # RLS hid it (or there is none): say so, so "no permission" is not read as "no budget"
                item["budget"] = None
                col.deny(f"hr.requisition_budget:{r['req_id']}", NOT_VISIBLE)
            items.append(item)
        return col.envelope({"requisitions": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "list_requisitions", body)


async def list_candidates(ctx: Context, req_id: str) -> dict:
    """Candidates of one requisition (no résumé text; use get_candidate)."""
    async def body(col: Collector) -> dict:
        rid = check_id("req_id", req_id)
        col.resource = f"hr.requisition:{rid}"
        async with tx(col) as conn:
            rows = await fetch_all(conn, "SELECT * FROM hr.candidates WHERE req_id = %s ORDER BY candidate_id", (rid,))
        items = [c for r in rows if (c := col.row("hr.candidate", r["candidate_id"], r, CANDIDATE_LIST_FIELDS))]
        if not items:
            col.deny(f"hr.requisition:{rid}", NOT_VISIBLE)
        return col.envelope({"req_id": rid, "candidates": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "list_candidates", body)


async def get_candidate(ctx: Context, candidate_id: str) -> dict:
    """One candidate with the résumé text (untrusted input)."""
    async def body(col: Collector) -> dict:
        cid = check_id("candidate_id", candidate_id)
        col.resource = f"hr.candidate:{cid}"
        async with tx(col) as conn:
            row = await fetch_one(conn, "SELECT * FROM hr.candidates WHERE candidate_id = %s", (cid,))
        cand = col.row("hr.candidate", cid, row, CANDIDATE_FIELDS) if row else None
        if cand is None:
            col.deny(f"hr.candidate:{cid}", NOT_VISIBLE)
            return col.error("not_found_or_denied", "no such candidate, or you have no permission to see them")
        return col.envelope({"candidate": cand})
    return await run_tool(ctx, DOMAIN, "get_candidate", body)


async def create_requisition(ctx: Context, dept: str, title: str, headcount: int, jd_text: str,
                             hard_requirements: list[str] | None = None) -> dict:
    """Propose opening a requisition (tier 2). The hiring manager is the department's head."""
    async def body(col: Collector) -> dict:
        dp = check_choice("dept", dept, DEPTS)
        if not isinstance(headcount, int) or isinstance(headcount, bool) or not 1 <= headcount <= 50:
            raise ToolArgError("headcount must be an integer between 1 and 50")
        reqs = hard_requirements or []
        if not isinstance(reqs, list) or len(reqs) > 20:
            raise ToolArgError("hard_requirements must be a list of at most 20 items")
        params = {"dept": dp, "title": check_text("title", title, max_len=200), "headcount": headcount,
                  "jd_text": check_text("jd_text", jd_text, max_len=20000),
                  "hard_requirements": [check_text("hard_requirements item", r, max_len=300) for r in reqs]}
        label = Label(level=Level.L2, dept=Dept(dp),
                      acl_groups=frozenset({"dept:hr", "head:hr", f"head:{dp}", "role:ceo"}))  # §5.1 requisition
        if not may_propose_new(col, "hr.requisition", label):
            return refused(col)
        return await file_proposal(col, tool="create_requisition", params=params,
                                   summary=f"Open requisition in {dp}: {params['title']} x{headcount}", level_floor=2)
    return await run_tool(ctx, DOMAIN, "create_requisition", body)


TOOLS = [get_employee, headcount, list_requisitions, list_candidates, get_candidate, create_requisition]
