"""Starting and resuming graph runs, and the run record of the API (phase-B spec §18).

Every run gets a fresh run_id and a fresh RunContext built from a principal re-read from the
directory. A resume after an approval runs as the thread's OWNER, never as the approver, and its
resume value is built here from the stored proposal, never from a request body.

One run at a time per thread. LangGraph does not serialise two invokes on one thread_id: both
start from the same parent checkpoint and the last to finish becomes the thread's state, so a
public question finishing after a confidential one would put the thread back at hwm L1 (the hwm
must only go up). A second request on a busy thread is refused; a resume waits for its turn.

The checkpointer may be a CheckpointStore (the API): every run then gets a saver bound to a
connection carrying the run principal, so checkpoint rows are only reachable through RLS as the
thread's owner (corpai/agents/_checkpoint.py). A plain saver (tests) is used as it is.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from corpai.agents import _gazetteer, _threads, router
from corpai.agents.runtime import RunContext, Services, current_hwm
from corpai.ids import new_id
from corpai.types import Level, Principal

log = logging.getLogger(__name__)

EMPTY_OUTPUT = {"text": "", "citations": [], "data": {}}


class RunRefused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def error_of(e: BaseException) -> dict:
    """§18 error code and a fixed message (never exception text that could quote data)."""
    from corpai.gateway import errors as ge
    from corpai.mcp_client import McpCallError

    table: list[tuple[type, str, str]] = [
        (ge.LocalUnavailable, "local_unavailable",
         "The local model is unavailable. Confidential requests never fall back to the external route; "
         "try again later."),
        (ge.ExternalUnavailable, "external_unavailable", "The external model was switched off; try again."),
        (ge.GatewayBusy, "gateway_busy", "The model is busy; try again in a few seconds."),
        (ge.BudgetExceeded, "budget_exceeded", "Your department's model budget for today is used up."),
        (ge.SecretDetected, "secret_detected", "The request contains a credential; remove it and try again."),
        (ge.GatewayError, "backend_error", "The model could not answer; try again later."),
        (McpCallError, "tool_unavailable", "A tool could not be reached; try again later."),
        (PermissionError, "forbidden", "Your account may not run this."),
    ]
    for cls, code, msg in table:
        if isinstance(e, cls):
            return {"code": code, "message": msg}
    return {"code": "internal_error", "message": "The run failed; try again or ask the platform admin."}


class ThreadBusy(RunRefused):
    def __init__(self) -> None:
        super().__init__(400, "bad_request", "This conversation is still answering; wait for it to finish, "
                                             "or start a new conversation.")


# thread_id -> lock, per process (`python -m corpai serve` is one process). Weak values: a lock
# disappears once no run holds or waits on it.
_thread_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _lock_for(thread_id: str) -> asyncio.Lock:
    lock = _thread_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[thread_id] = lock
    return lock


@asynccontextmanager
async def _thread_turn(thread_id: str, *, wait: bool) -> AsyncIterator[None]:
    lock = _lock_for(thread_id)
    if lock.locked() and not wait:
        raise ThreadBusy()
    async with lock:
        yield


@asynccontextmanager
async def _graph_for(route: Any, checkpointer: Any, p: Principal, run_id: str | None) -> AsyncIterator[Any]:
    """The compiled graph with a saver acting as `p` (CheckpointStore) or the given saver."""
    bind = getattr(checkpointer, "for_principal", None)
    if bind is None:
        yield router.compiled_graph(route, checkpointer)
        return
    async with bind(p, run_id=run_id) as saver:
        yield router.compiled_graph(route, None).copy(update={"checkpointer": saver})


def _interrupts(snap: Any) -> list[Any]:
    vals = list(getattr(snap, "interrupts", None) or ())
    if not vals:
        for task in getattr(snap, "tasks", None) or ():
            vals.extend(getattr(task, "interrupts", None) or ())
    return [getattr(i, "value", i) for i in vals]


def _approval_wait(snap: Any) -> str | None:
    for v in _interrupts(snap):
        if isinstance(v, dict) and v.get("type") == "approval" and isinstance(v.get("proposal_id"), str):
            return v["proposal_id"]
    return None


async def _pending_info(p: Principal, proposal_id: str) -> dict:
    from corpai import proposals

    prop = await proposals.get(p, proposal_id)
    if prop is None:
        return {"proposal_id": proposal_id, "tool": None, "tier": None, "summary": None}
    return {"proposal_id": prop.id, "tool": f"{prop.domain}.{prop.tool}", "tier": int(prop.tier),
            "summary": prop.summary}


async def _execute(graph: Any, inp: Any, *, ctx: RunContext, app: str) -> dict:
    config = {"configurable": {"thread_id": ctx.thread_id}}
    before = await graph.aget_state(config)
    n0 = len((before.values or {}).get("events") or [])
    error: dict | None = None
    try:
        await graph.ainvoke(inp, config, context=ctx)
    except Exception as e:  # noqa: BLE001 - mapped to a fixed §18 error below
        error = error_of(e)
        if error["code"] == "internal_error":
            log.warning("run %s failed: %s", ctx.run_id, type(e).__name__)
    snap = await graph.aget_state(config)
    values = snap.values or {}
    events = list((values.get("events") or [])[n0:])
    hwm = int(current_hwm(values))
    if error and error["code"] == "local_unavailable":
        events.append({"t": "route", "route": "local", "level": max(hwm, int(Level.L3)), "reason": "local_down"})
    pending = None
    if error is None:
        pid = _approval_wait(snap)
        if pid is not None:
            pending = await _pending_info(ctx.principal, pid)
    route = next((e.get("route") for e in reversed(events) if e.get("t") == "route"), None)
    status = "error" if error else ("waiting_approval" if pending else "done")
    output = values.get("output") if (error is None and isinstance(values.get("output"), dict)) else None
    return {"run_id": ctx.run_id, "thread_id": ctx.thread_id, "app": app, "status": status,
            "output": output or dict(EMPTY_OUTPUT), "hwm": hwm, "route": route, "events": events,
            "pending_approval": pending, "error": error}


async def start_run(p: Principal, *, app: Any, text: str, thread_id: str | None, user_level: Level,
                    services: Services, checkpointer: Any) -> dict:
    route = router.resolve(p, app)  # RouterError: unknown app, may not start, not ready
    await _gazetteer.ensure_fresh()  # names added by a synth/ingest in another process count at once
    if thread_id is not None:
        row = await _threads.get_own(p, thread_id)
        if row is None:
            raise RunRefused(404, "not_found", "No such conversation among yours; start a new one.")
        if row["app"] != route.app:
            raise RunRefused(400, "bad_request", "This conversation belongs to another app.")
    else:
        thread_id = new_id("thr")
        await _threads.create(p, thread_id, agent=route.agent, app=route.app)
    run_id = new_id("run")
    async with _thread_turn(thread_id, wait=False), _graph_for(route, checkpointer, p, run_id) as graph:
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        waiting = _approval_wait(snap)
        if waiting is not None:
            resume = await _late_resume(p, waiting)
            if resume is None:
                raise RunRefused(400, "bad_request", "This conversation is waiting for an approval; decide it "
                                                     "first or start a new conversation.")
            # The proposal was decided (or lapsed) but the thread never got the answer, e.g. the
            # resume after the decision failed: hand it over now, then take the new input.
            from langgraph.types import Command

            late_id = new_id("run")
            await _threads.add_run(p, late_id, thread_id)
            late = RunContext(principal=p, run_id=late_id, thread_id=thread_id, agent=route.agent,
                              services=services, user_level=user_level)
            done = await _execute(graph, Command(resume=resume), ctx=late, app=route.app)
            if done["status"] != "done":
                return done
        await _threads.add_run(p, run_id, thread_id)
        ctx = RunContext(principal=p, run_id=run_id, thread_id=thread_id, agent=route.agent, services=services,
                         user_level=user_level)
        return await _execute(graph, route.initial_state(text), ctx=ctx, app=route.app)


async def _late_resume(owner: Principal, proposal_id: str) -> dict | None:
    """The resume value for a thread whose proposal is already decided, or None while a person
    still has to decide (or the executor is running)."""
    from datetime import datetime, timezone

    from corpai import proposals

    prop = await proposals.get(owner, proposal_id)
    if prop is None:
        return {"proposal_id": proposal_id, "approved": False, "executed": False, "status": "missing",
                "result": None}
    if prop.status in ("executed", "failed", "rejected", "expired"):
        return resume_value(prop)
    if prop.status == "pending" and prop.expires_at <= datetime.now(timezone.utc):
        return {"proposal_id": prop.id, "approved": False, "executed": False, "status": "expired", "result": None}
    return None


def resume_value(prop: Any) -> dict:
    """What approval_gate returns to the graph. `approved` is True only when the approved action
    actually ran (status executed): an approval whose executor refused or failed (status failed)
    resumes as not approved, so no graph tells its user that a mail or contract went out."""
    executed = prop.status == "executed"
    return {"proposal_id": prop.id, "approved": executed, "executed": executed, "status": prop.status,
            "result": prop.result}


async def resume_for_proposal(decider: Principal, prop: Any, *, services: Services,
                              checkpointer: Any) -> dict | None:
    """Resume the requester's thread waiting on `prop` after `decider` decided it. None when no
    thread waits on it (filed outside a graph, pruned, or the owner was deactivated)."""
    from langgraph.types import Command

    from corpai.identity import directory

    row = await _threads.for_proposal(decider, prop.id)
    if row is None:
        return None
    owner = await directory.get_principal(row["owner_id"])
    if owner is None or not owner.active:
        return None  # a deactivated owner's thread stays paused
    try:
        route = router.resolve(owner, row["app"])
    except router.RouterError:
        return None
    await _gazetteer.ensure_fresh()
    run_id = new_id("run")
    # The decision is made: wait for a run still holding the thread instead of dropping the resume.
    async with _thread_turn(row["thread_id"], wait=True), _graph_for(route, checkpointer, owner, run_id) as graph:
        snap = await graph.aget_state({"configurable": {"thread_id": row["thread_id"]}})
        if _approval_wait(snap) != prop.id:
            return None
        await _threads.add_run(owner, run_id, row["thread_id"])
        ctx = RunContext(principal=owner, run_id=run_id, thread_id=row["thread_id"], agent=route.agent,
                         services=services)
        return await _execute(graph, Command(resume=resume_value(prop)), ctx=ctx, app=route.app)


def for_viewer(run: dict | None, viewer: Principal, owner_id: str | None) -> dict | None:
    """A resumed run as the approver may see it: status and route, not the owner's content."""
    if run is None or owner_id is None or viewer.user_id == owner_id:
        return run
    out = dict(run)
    out["output"] = dict(EMPTY_OUTPUT)
    out["events"] = [e for e in run.get("events") or [] if e.get("t") in ("route", "approval", "hwm", "notice")]
    return out
