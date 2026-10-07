"""The HR person's screening decision and the resume of the waiting hr_screening thread.

Order, all under the thread's run lock (one run per thread, corpai/agents/_runner.py):
1. the thread is the caller's own hr_screening thread (unknown and someone else's look the same);
2. it waits for a decision on exactly this candidate (the interrupt the graph raised);
3. corpai.hr.record_decision as the caller: HR staff on the requisition or the head of HR, a
   reason is mandatory, every attempt audited (event hr_decision), refusals included;
4. only then the thread resumes, with a value built here by code: {type, candidate_id, decision,
   screening_id, decided_by}. The reason stays in hr.screenings (L4), never in graph state.

The model has no part in this: record_decision is not a tool, and no graph node can call it.
"""

from __future__ import annotations

from typing import Any

from corpai.agents import _runner, _threads, router
from corpai.agents.hr.screening import APP, INTERRUPT_TYPE
from corpai.agents.runtime import RunContext, Services
from corpai.hr import record_decision
from corpai.ids import new_id
from corpai.types import Principal


class DecisionRefused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _waiting_on(snap: Any) -> dict | None:
    vals = list(getattr(snap, "interrupts", None) or ())
    if not vals:
        for task in getattr(snap, "tasks", None) or ():
            vals.extend(getattr(task, "interrupts", None) or ())
    for v in (getattr(i, "value", i) for i in vals):
        if isinstance(v, dict) and v.get("type") == INTERRUPT_TYPE:
            return v
    return None


async def decide(p: Principal, *, thread_id: str, candidate_id: str, decision: str, reason: str,
                 services: Services, checkpointer: Any) -> dict:
    """Record the caller's decision and resume their screening thread. Raises DecisionRefused,
    router.RouterError, _runner.RunRefused (busy thread), corpai.hr.NoPermission, ValueError."""
    row = await _threads.get_own(p, thread_id)
    if row is None:
        raise DecisionRefused(404, "not_found", "No such conversation among yours.")
    if row["app"] != APP:
        raise DecisionRefused(400, "bad_request", "This conversation is not a résumé screening.")
    route = router.resolve(p, APP)
    run_id = new_id("run")
    # the same lock and saver binding the runner uses: a double click cannot record twice
    async with _runner._thread_turn(thread_id, wait=False), \
            _runner._graph_for(route, checkpointer, p, run_id) as graph:
        snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        waiting = _waiting_on(snap)
        if waiting is None or waiting.get("candidate_id") != candidate_id:
            raise DecisionRefused(400, "bad_request", "This conversation is not waiting for a decision on that "
                                                      "candidate; screen the candidate first.")
        screening_id = await record_decision(p, candidate_id=candidate_id, decision=decision, reason=reason)
        from langgraph.types import Command

        await _threads.add_run(p, run_id, thread_id)
        ctx = RunContext(principal=p, run_id=run_id, thread_id=thread_id, agent=route.agent, services=services)
        value = {"type": INTERRUPT_TYPE, "candidate_id": candidate_id, "decision": decision,
                 "screening_id": screening_id, "decided_by": p.user_id}
        run = await _runner._execute(graph, Command(resume=value), ctx=ctx, app=APP)
    return {"screening_id": screening_id, "decision": decision, "candidate_id": candidate_id, "run": run}
