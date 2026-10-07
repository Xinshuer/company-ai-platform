"""code server: projects, tickets, CI runs, repo docs (architecture §11).

Projects are L3 for eng, sales and the CEO (stage-level status); tickets and CI runs L3 for the
project's members and head:eng (§5.1).
"""

from __future__ import annotations

from mcp.server.mcpserver import Context

from corpai.mcp_servers import _rag
from corpai.mcp_servers._common import (
    check_choice,
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

DOMAIN = "code"
PROJECT_STATUS_FIELDS = ["project_id", "name", "stage", "stage_since", "planned_go_live", "health"]
TICKET_LIST_FIELDS = ["ticket_id", "project_id", "title", "type", "status", "assignee_id", "created_on", "updated_on"]
TICKET_FIELDS = TICKET_LIST_FIELDS + ["description"]
CI_FIELDS = ["run_id", "project_id", "branch", "status", "started_at", "commit_sha", "failing_tests"]
COMMENT_FIELDS = ["comment_id", "author_id", "body", "created_at"]
TICKET_STATUSES = ("open", "in_progress", "in_review", "done")
TICKET_TYPES = ("bug", "story", "task", "chore")


async def search_tickets(ctx: Context, query: str = "", project_id: str | None = None, status: str | None = None,
                         limit: int = 20) -> dict:
    """Tickets you may see whose title or description contains `query`."""
    async def body(col: Collector) -> dict:
        q, lim = search_text(query), check_limit(limit, default=20, maximum=100)
        pid = opt_id("project_id", project_id)
        st = opt_choice("status", status, TICKET_STATUSES)
        async with tx(col) as conn:
            rows = await fetch_all(conn,
                                   "SELECT * FROM eng.tickets WHERE (%s = '' OR title ILIKE '%%' || %s || '%%'"
                                   " OR description ILIKE '%%' || %s || '%%') AND (%s::text IS NULL OR project_id = %s)"
                                   " AND (%s::text IS NULL OR status = %s) ORDER BY updated_on DESC, ticket_id LIMIT %s",
                                   (q, q, q, pid, pid, st, st, lim))
        items = [t for r in rows if (t := col.row("eng.ticket", r["ticket_id"], r, TICKET_LIST_FIELDS))]
        return col.envelope({"tickets": items, "count": len(items)})
    return await run_tool(ctx, DOMAIN, "search_tickets", body)


async def get_ticket(ctx: Context, ticket_id: str) -> dict:
    """One ticket with its description and comments."""
    async def body(col: Collector) -> dict:
        tid = check_id("ticket_id", ticket_id)
        col.resource = f"eng.ticket:{tid}"
        async with tx(col) as conn:
            row = await fetch_one(conn, "SELECT * FROM eng.tickets WHERE ticket_id = %s", (tid,))
            comments = await fetch_all(conn, "SELECT * FROM eng.ticket_comments WHERE ticket_id = %s"
                                             " ORDER BY created_at", (tid,))
        ticket = col.row("eng.ticket", tid, row, TICKET_FIELDS) if row else None
        if ticket is None:
            col.deny(f"eng.ticket:{tid}", NOT_VISIBLE)
            return col.error("not_found_or_denied", "no such ticket, or you have no permission to see it")
        ticket["comments"] = [c for r in comments if (c := col.row("eng.ticket", r["comment_id"], r, COMMENT_FIELDS))]
        return col.envelope({"ticket": ticket})
    return await run_tool(ctx, DOMAIN, "get_ticket", body)


async def list_ci_runs(ctx: Context, project_id: str, limit: int = 20) -> dict:
    """Latest CI runs of one project."""
    async def body(col: Collector) -> dict:
        pid = check_id("project_id", project_id)
        lim = check_limit(limit, default=20, maximum=100)
        col.resource = f"eng.project:{pid}"
        async with tx(col) as conn:
            rows = await fetch_all(conn, "SELECT * FROM eng.ci_runs WHERE project_id = %s"
                                         " ORDER BY started_at DESC LIMIT %s", (pid, lim))
        runs = [r2 for r in rows if (r2 := col.row("eng.ci_run", r["run_id"], r, CI_FIELDS))]
        if not runs:
            col.deny(f"eng.project:{pid}", NOT_VISIBLE)
        return col.envelope({"project_id": pid, "ci_runs": runs, "count": len(runs)})
    return await run_tool(ctx, DOMAIN, "list_ci_runs", body)


async def get_project_status(ctx: Context, project_id: str | None = None) -> dict:
    """Stage-level status of one project, or of every project you may see."""
    async def body(col: Collector) -> dict:
        pid = opt_id("project_id", project_id)
        col.resource = f"eng.project:{pid}" if pid else None
        async with tx(col) as conn:
            rows = await fetch_all(conn, "SELECT * FROM eng.projects WHERE %s::text IS NULL OR project_id = %s"
                                         " ORDER BY project_id", (pid, pid))
        projects = [p for r in rows if (p := col.row("eng.project", r["project_id"], r, PROJECT_STATUS_FIELDS))]
        if pid and not projects:
            col.deny(f"eng.project:{pid}", NOT_VISIBLE)
            return col.error("not_found_or_denied", "no such project, or you have no permission to see it")
        return col.envelope({"projects": projects, "count": len(projects)})
    return await run_tool(ctx, DOMAIN, "get_project_status", body)


async def search_code(ctx: Context, query: str, k: int = 8) -> dict:
    """Search repository documents (READMEs, ADRs, runbooks) you may read."""
    async def body(col: Collector) -> dict:
        q = check_text("query", query, max_len=500)
        kk = check_limit(k, default=8, maximum=20)
        return await _rag.search(col, q, k=kk, purpose="qa", collections=["eng"])
    return await run_tool(ctx, DOMAIN, "search_code", body)


# ---- write tools: proposals only ------------------------------------------------------------

async def create_ticket(ctx: Context, project_id: str, title: str, description: str, type: str = "task") -> dict:
    """Propose a new ticket in a project (tier 2)."""
    async def body(col: Collector) -> dict:
        pid = check_id("project_id", project_id)
        params = {"project_id": pid, "title": check_text("title", title, max_len=200),
                  "description": check_text("description", description, max_len=10000),
                  "type": check_choice("type", type, TICKET_TYPES)}
        col.resource = f"eng.project:{pid}"
        async with tx(col) as conn:
            proj = await fetch_one(conn, "SELECT * FROM eng.projects WHERE project_id = %s", (pid,))
        if not may_propose_on(col, "eng.project", pid, proj):
            return refused(col)
        return await file_proposal(col, tool="create_ticket", params=params,
                                   summary=f"New {params['type']} in {pid}: {params['title']}", level_floor=3)
    return await run_tool(ctx, DOMAIN, "create_ticket", body)


async def comment_ticket(ctx: Context, ticket_id: str, comment: str) -> dict:
    """Propose a comment on a ticket (tier 2)."""
    async def body(col: Collector) -> dict:
        tid = check_id("ticket_id", ticket_id)
        params = {"ticket_id": tid, "comment": check_text("comment", comment, max_len=10000)}
        col.resource = f"eng.ticket:{tid}"
        async with tx(col) as conn:
            t = await fetch_one(conn, "SELECT * FROM eng.tickets WHERE ticket_id = %s", (tid,))
        if not may_propose_on(col, "eng.ticket", tid, t):
            return refused(col)
        return await file_proposal(col, tool="comment_ticket", params=params, summary=f"Comment on {tid}",
                                   level_floor=int(t["level"]))
    return await run_tool(ctx, DOMAIN, "comment_ticket", body)


TOOLS = [search_tickets, get_ticket, list_ci_runs, get_project_status, search_code, create_ticket, comment_ticket]
