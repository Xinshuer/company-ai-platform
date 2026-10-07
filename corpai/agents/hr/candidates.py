"""hr_candidates: the hiring manager's read-only view of their own requisitions' candidates (D-14).

Which requisitions: the caller's req:<id> memberships (code, from the directory: the hiring manager
and the recruiter of a requisition hold it), or the one id the caller asked for. Heads, HR and the
CEO may also list requisitions (titles); nobody gets candidates without the tool rule
tool-hris-hiring-manager (req:<id>) or HR's own rule, and RLS on hr.candidates limits the rows.
Everything else is an explicit denial. There is no model call, no write tool, and no screening
evidence (L4, HR only) anywhere in this app.

`collect()` is shared by the graph (tools through the runtime helper, so the hwm and events are
kept) and by GET /api/hr/candidates (tools through the same MCP client, same agent, same rules).
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from langgraph.graph import END, START, StateGraph

from corpai.agents.runtime import BaseState, RunContext, Step, node
from corpai.policy import tool_allowed
from corpai.types import Level, Principal

APP = "hr_candidates"
REQ_RE = re.compile(r"\bR-[0-9]{2}\b")
MAX_REQS = 10
NOT_A_HIRING_MANAGER = "you are not the hiring manager or recruiter of any requisition"

ToolCall = Callable[[str, str, dict], Awaitable[Any]]  # (domain, tool, args) -> ToolEnvelope


def own_requisitions(p: Principal) -> list[str]:
    return sorted(g.removeprefix("req:") for g in p.groups if g.startswith("req:") and REQ_RE.fullmatch(g[4:]))


async def collect(p: Principal, agent: str, call: ToolCall, *, requested: list[str]) -> dict:
    """{requisitions: [{req_id, title, status, own, candidates, denied}], denied: [{id, reason}]}."""
    denied: list[dict] = []
    titles: dict[str, dict] = {}
    if tool_allowed(p, agent, "hris", "list_requisitions").allow:
        env = await call("hris", "list_requisitions", {})
        rows = env.data.get("requisitions") if env.ok and isinstance(env.data, dict) else None
        for r in rows or []:
            if isinstance(r, dict) and r.get("req_id"):
                titles[r["req_id"]] = {"title": r.get("title"), "status": r.get("status"), "dept": r.get("dept")}
    own = own_requisitions(p)
    # Asked for one id: that one. Otherwise the caller's own requisitions; with none, the open ones
    # the caller may list (HR, heads, CEO), each answered or explicitly denied below.
    listed_open = sorted(r for r, m in titles.items() if m.get("status") in ("open", "on_hold"))
    reqs = list(dict.fromkeys(requested or own or listed_open))[:MAX_REQS]
    if not reqs:
        return {"requisitions": [], "denied": [{"id": "hr.requisition:*", "reason": NOT_A_HIRING_MANAGER}],
                "own": own, "listed": sorted(titles)}
    allowed = tool_allowed(p, agent, "hris", "list_candidates")
    out = []
    for req_id in reqs:
        meta = titles.get(req_id, {})
        item = {"req_id": req_id, "title": meta.get("title"), "status": meta.get("status"), "own": req_id in own,
                "candidates": [], "denied": False}
        if not allowed.allow:
            # decided by the same policy the tool server applies; no call is made
            item["denied"] = True
            denied.append({"id": f"hr.requisition:{req_id}", "reason": "no permission"})
        else:
            env = await call("hris", "list_candidates", {"req_id": req_id})
            rows = (env.data or {}).get("candidates") if env.ok and isinstance(env.data, dict) else None
            item["candidates"] = [{k: c.get(k) for k in ("candidate_id", "name", "stage", "applied_on", "source")}
                                  for c in rows or [] if isinstance(c, dict)]
            if not item["candidates"]:
                item["denied"] = True
                denied.extend({"id": str(d.get("id")), "reason": str(d.get("reason") or "")[:200]}
                              for d in env.denied if isinstance(d, dict))
        out.append(item)
    return {"requisitions": out, "denied": denied, "own": own, "listed": sorted(titles)}


class CandidatesState(BaseState, total=False):
    question: str
    output: dict | None


def initial_state(text: str) -> dict:
    return {"question": text, "output": None}


@node(caps={"sensitive_data", "untrusted_input"})
async def candidates(state: CandidatesState, runtime: Any) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    # The user's raised level is a floor for the session (design-review 四.2 rule 3), whatever the
    # tool calls below add; a later run in this thread starts from it.
    user_level = Level(max(1, min(4, int(ctx.user_level))))
    if user_level > Level.L1:
        step.raise_to(user_level, "user_level")

    async def call(domain: str, tool: str, args: dict) -> Any:
        return await step.call_tool(domain, tool, args)

    requested = REQ_RE.findall(state.get("question") or "")
    data = await collect(ctx.principal, ctx.agent, call, requested=requested)
    n = sum(len(r["candidates"]) for r in data["requisitions"])
    text = (f"{n} candidate(s) on {len(data['requisitions'])} requisition(s)." if data["requisitions"]
            else "No requisition of yours: " + NOT_A_HIRING_MANAGER + ".")
    return step.update(output={"text": text, "citations": [], "data": {"kind": APP, **data}})


def build_graph() -> StateGraph:
    g = StateGraph(CandidatesState, context_schema=RunContext)
    g.add_node("candidates", candidates)
    g.add_edge(START, "candidates")
    g.add_edge("candidates", END)
    return g
