"""Approval round trip (architecture §11-§13, phase-B spec §18): a graph files a crm.send_email
proposal and waits; four-eyes applies; the head of sales approves with the exact params hash; the
executor writes the outbox; the waiting thread resumes as its owner, not as the approver."""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from corpai.agents import BaseState, RunContext, Step, approval_gate, node, router
from tests.api.support import login, owner_rows, run

pytestmark = [pytest.mark.anyio, pytest.mark.security]

APP = "b3_test_mail"


class MailState(BaseState, total=False):
    question: str
    proposal_id: str | None
    decision: dict
    output: dict


def _builder(account_id: str, to: str):
    @node(caps={"sensitive_data", "external_action"})
    async def propose(state, runtime):
        step = Step(runtime, state)
        env = await step.call_tool("crm", "send_email", {"account_id": account_id, "to": to,
                                                         "subject": "Follow-up on route planning",
                                                         "body": "Thank you for the call; here is the summary."})
        assert env.ok, env
        return step.update(proposal_id=env.proposal_id)

    @node(caps=set(), gate=True)
    async def wait(state, runtime):
        return {"decision": await approval_gate(runtime, state["proposal_id"])}

    @node(caps={"sensitive_data"})
    async def finish(state, runtime):
        d = state["decision"]
        sent = d.get("approved") and d.get("status") == "executed"
        return {"output": {"text": "sent" if sent else f"not sent ({d.get('status')})", "citations": [],
                           "data": {"by": runtime.context.principal.user_id, "result": d.get("result")}}}

    def build():
        g = StateGraph(MailState, context_schema=RunContext)
        g.add_node("propose", propose)
        g.add_node("wait", wait)
        g.add_node("finish", finish)
        g.add_edge(START, "propose")
        g.add_edge("propose", "wait")
        g.add_edge("wait", "finish")
        g.add_edge("finish", END)
        return g
    return build


@pytest.fixture
def mail_app(seeded, personas):
    row = owner_rows("SELECT a.account_id, c.email FROM crm.accounts a JOIN crm.contacts c USING (account_id)"
                     " WHERE a.owner_id = %s ORDER BY a.account_id, c.contact_id LIMIT 1",
                     (personas["sales_staff"].user_id,))[0]
    router.register_app(APP, agent="sales_profile", builder=_builder(row["account_id"], row["email"]),
                        initial_state=lambda text: {"question": text})
    yield row
    router.unregister_app(APP)


async def _start(client: Any) -> dict:
    r = await run(client, "Send the follow-up", app=APP)
    assert r["status"] == "waiting_approval", r
    return r


async def test_approval_round_trip(app_client, personas, mail_app):
    staff, head = personas["sales_staff"], personas["sales_head"]
    await login(app_client, staff.user_id)
    r = await _start(app_client)
    pending = r["pending_approval"]
    assert pending["tool"] == "crm.send_email" and pending["tier"] == 3 and pending["proposal_id"].startswith("prop_")
    assert {"t": "approval", "proposal_id": pending["proposal_id"], "tier": 3} in r["events"]
    pid = pending["proposal_id"]

    # four-eyes: the requester cannot approve a tier-3 proposal
    mine = (await app_client.get("/api/proposals?box=mine")).json()
    prop = next(p for p in mine if p["proposal_id"] == pid)
    assert prop["can_approve"] is False and prop["params"]["to"] == mail_app["email"]
    resp = await app_client.post(f"/api/proposals/{pid}/approve", json={"params_hash": prop["params_hash"],
                                                                      "reason": "looks fine"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "forbidden"
    # the waiting thread takes no new input until someone decides
    again = await app_client.post("/api/runs", json={"app": APP, "input": "again", "thread_id": r["thread_id"]})
    assert again.status_code == 400

    # someone outside sales cannot even see it
    await login(app_client, personas["eng_head"].user_id)
    resp = await app_client.post(f"/api/proposals/{pid}/approve", json={"params_hash": prop["params_hash"],
                                                                      "reason": "x"})
    assert resp.status_code == 404

    await login(app_client, head.user_id)
    inbox = (await app_client.get("/api/proposals?box=to_approve")).json()
    item = next(p for p in inbox if p["proposal_id"] == pid)
    assert item["can_approve"] is True and item["params_hash"] == prop["params_hash"]
    assert item["requester"]["user_id"] == staff.user_id
    bad = await app_client.post(f"/api/proposals/{pid}/approve", json={"params_hash": "0" * 64, "reason": "ok"})
    assert bad.status_code == 400
    no_reason = await app_client.post(f"/api/proposals/{pid}/approve",
                                      json={"params_hash": item["params_hash"], "reason": "  "})
    assert no_reason.status_code == 400
    assert owner_rows("SELECT count(*) AS n FROM ops.outbox WHERE proposal_id = %s", (pid,))[0]["n"] == 0

    ok = await app_client.post(f"/api/proposals/{pid}/approve",
                               json={"params_hash": item["params_hash"], "reason": "matches the call notes"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["proposal"]["status"] == "executed" and body["proposal"]["approver_id"] == head.user_id
    resumed = body["run"]
    assert resumed["status"] == "done" and resumed["thread_id"] == r["thread_id"]
    assert resumed["run_id"] != r["run_id"]
    assert resumed["output"]["text"] == ""  # the approver does not get the owner's thread content
    out = owner_rows("SELECT recipient FROM ops.outbox WHERE proposal_id = %s", (pid,))
    assert out == [{"recipient": mail_app["email"]}]

    # the resumed run ran as the thread's owner: its trace is the owner's, not the approver's
    assert (await app_client.get(f"/api/runs/{resumed['run_id']}/trace")).status_code == 404
    await login(app_client, staff.user_id)
    t = await app_client.get(f"/api/runs/{resumed['run_id']}/trace")
    assert t.status_code == 200
    names = {s["name"] for s in t.json()["spans"]}
    assert {"node:wait", "node:finish", "approval"} <= names
    # deciding again is refused, and does not resume anything
    await login(app_client, head.user_id)
    twice = await app_client.post(f"/api/proposals/{pid}/approve",
                                  json={"params_hash": item["params_hash"], "reason": "again"})
    assert twice.status_code == 400


async def test_withdrawal_resumes_the_owner_with_not_sent(app_client, personas, mail_app):
    await login(app_client, personas["sales_staff"].user_id)
    r = await _start(app_client)
    pid = r["pending_approval"]["proposal_id"]
    resp = await app_client.post(f"/api/proposals/{pid}/reject", json={"reason": "wrong contact"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposal"]["status"] == "rejected"
    assert body["run"]["status"] == "done" and body["run"]["output"]["text"] == "not sent (rejected)"
    assert body["run"]["output"]["data"]["by"] == personas["sales_staff"].user_id
    assert owner_rows("SELECT count(*) AS n FROM ops.outbox WHERE proposal_id = %s", (pid,))[0]["n"] == 0
    # the thread is free again
    nxt = await app_client.post("/api/runs", json={"app": APP, "input": "retry", "thread_id": r["thread_id"]})
    assert nxt.status_code == 200 and nxt.json()["status"] == "waiting_approval"


async def test_thread_lookup_is_only_for_the_decider(app_client, personas, mail_app):
    from corpai.agents import _threads

    await login(app_client, personas["sales_staff"].user_id)
    r = await _start(app_client)
    pid = r["pending_approval"]["proposal_id"]
    # pending: the head may approve but has not decided, so the thread is not handed out yet
    assert await _threads.for_proposal(personas["sales_head"], pid) is None
    assert await _threads.for_proposal(personas["eng_staff"], pid) is None
    assert (await _threads.for_proposal(personas["sales_staff"], pid))["thread_id"] == r["thread_id"]
