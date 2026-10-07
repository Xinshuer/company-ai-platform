"""Review fixes on the API path (phase B): the live server never hands out the demo fixtures,
checkpoints are under row-level security per thread owner, one run at a time per thread (the hwm
can never drop back), a thread whose decision got lost is resumed on its next input, and the
gazetteer follows name changes made by synth/ingest in another process."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from corpai.agents import BaseState, RunContext, Step, approval_gate, node, router
from corpai.db import pg
from tests.api.support import login, owner_exec, owner_rows, run

pytestmark = pytest.mark.anyio

ACCOUNT_Q = "Which of my customer accounts need attention?"
PUBLIC_Q = "What does FleetPilot do?"


# ---- static files --------------------------------------------------------------------------------

@pytest.mark.security
@pytest.mark.parametrize("path", ["/fixtures/runs.js", "/fixtures/audit.js", "/fixtures/traces.js",
                                  "/FIXTURES/runs.js", "/fixtures./runs.js", "/js/../fixtures/runs.js",
                                  "/fixtures/", "/fixtures"])
async def test_live_server_never_serves_the_demo_fixtures(app_client, personas, path):
    for logged_in in (False, True):
        if logged_in:
            await login(app_client, personas["ceo"].user_id)
        else:
            app_client.cookies.clear()
        r = await app_client.get(path)
        assert r.status_code == 404, (path, logged_in)
        assert "window.FIXTURES" not in r.text


async def test_static_ui_is_not_cached(app_client):
    for path in ("/", "/js/app.js", "/js/fixtures_base.js"):
        r = await app_client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", path


# ---- checkpoints under RLS -----------------------------------------------------------------------

@pytest.mark.security
async def test_checkpoints_are_invisible_without_the_owner(app_client, personas):
    owner = personas["sales_staff"]
    await login(app_client, owner.user_id)
    r = await run(app_client, ACCOUNT_Q)
    tid = r["thread_id"]
    assert owner_rows("SELECT count(*) AS n FROM lg.checkpoints WHERE thread_id = %s", (tid,))[0]["n"] > 0
    tables = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
    with pg.connect_app() as conn:  # the app role with no principal
        for t in tables:
            assert conn.execute(f"SELECT count(*) AS n FROM lg.{t}").fetchone()["n"] == 0, t
    for other in ("sales_head", "ceo", "platform_admin"):
        with pg.principal_tx_sync(personas[other]) as conn:
            for t in tables:
                n = conn.execute(f"SELECT count(*) AS n FROM lg.{t} WHERE thread_id = %s", (tid,)).fetchone()["n"]
                assert n == 0, (other, t)
            # and cannot overwrite it either
            assert conn.execute("UPDATE lg.checkpoints SET metadata = '{}'::jsonb WHERE thread_id = %s",
                                (tid,)).rowcount == 0
    with pg.principal_tx_sync(owner) as conn:
        assert conn.execute("SELECT count(*) AS n FROM lg.checkpoints WHERE thread_id = %s",
                            (tid,)).fetchone()["n"] > 0
    # the thread still continues normally through the API
    again = await run(app_client, PUBLIC_Q, thread_id=tid)
    assert again["status"] == "done" and again["hwm"] >= r["hwm"]


@pytest.mark.security
async def test_checkpoint_rows_cannot_be_planted_in_someone_elses_thread(app_client, personas):
    await login(app_client, personas["sales_staff"].user_id)
    tid = (await run(app_client, PUBLIC_Q))["thread_id"]
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pg.principal_tx_sync(personas["sales_head"]) as conn:
            conn.execute("INSERT INTO lg.checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,"
                         " channel, type, blob) VALUES (%s, '', 'x', 't', 0, 'c', 'json', ''::bytea)", (tid,))


# ---- one run at a time per thread -----------------------------------------------------------------

SLOW_APP = "b3_test_slow"


class SlowState(BaseState, total=False):
    question: str
    output: dict


def _slow_builder(gate: dict):
    @node(caps={"sensitive_data"})
    async def work(state, runtime):
        if state.get("question") == "secret":
            await gate["release"].wait()
            return {"sensitivity_hwm": 3, "output": {"text": "secret", "citations": [], "data": {}}}
        return {"output": {"text": "public", "citations": [], "data": {}}}

    def build():
        g = StateGraph(SlowState, context_schema=RunContext)
        g.add_node("work", work)
        g.add_edge(START, "work")
        g.add_edge("work", END)
        return g
    return build


@pytest.fixture
def slow_app(seeded):
    gate: dict[str, Any] = {}
    router.register_app(SLOW_APP, agent="qa", builder=_slow_builder(gate))
    yield gate
    router.unregister_app(SLOW_APP)


@pytest.mark.security
async def test_a_second_run_on_a_busy_thread_is_refused_and_the_hwm_never_drops(app_client, personas, slow_app):
    await login(app_client, personas["sales_staff"].user_id)
    first = await run(app_client, "warm up", app=SLOW_APP)
    tid = first["thread_id"]
    slow_app["release"] = asyncio.Event()
    secret = asyncio.create_task(app_client.post("/api/runs", json={"app": SLOW_APP, "input": "secret",
                                                                     "thread_id": tid}))
    for _ in range(200):  # wait until the secret run holds the thread
        await asyncio.sleep(0.01)
        from corpai.agents import _runner

        lock = _runner._thread_locks.get(tid)
        if lock is not None and lock.locked():
            break
    busy = await app_client.post("/api/runs", json={"app": SLOW_APP, "input": "public", "thread_id": tid})
    assert busy.status_code == 400 and "still answering" in busy.json()["error"]["message"]
    slow_app["release"].set()
    done = (await secret).json()
    assert done["hwm"] == 3
    after = await run(app_client, "public", app=SLOW_APP, thread_id=tid)
    assert after["hwm"] == 3  # the confidential run's level stuck; nothing lowered it


# ---- a lost resume is delivered on the next input ---------------------------------------------------

MAIL_APP = "b3_test_mail2"


class MailState(BaseState, total=False):
    question: str
    proposal_id: str | None
    decision: dict
    output: dict


def _mail_builder(account_id: str, to: str):
    @node(caps={"sensitive_data", "external_action"})
    async def propose(state, runtime):
        if state.get("question") != "send":
            return {"output": {"text": "nothing to send", "citations": [], "data": {}}, "proposal_id": None}
        step = Step(runtime, state)
        env = await step.call_tool("crm", "send_email", {"account_id": account_id, "to": to, "subject": "Hello",
                                                         "body": "Short follow-up."})
        return step.update(proposal_id=env.proposal_id)

    @node(caps=set(), gate=True)
    async def wait(state, runtime):
        if not state.get("proposal_id"):
            return {}
        return {"decision": await approval_gate(runtime, state["proposal_id"])}

    @node(caps={"sensitive_data"})
    async def finish(state, runtime):
        d = state.get("decision") or {}
        if not state.get("proposal_id"):
            return {}
        return {"output": {"text": "sent" if d.get("approved") else f"not sent ({d.get('status')})",
                           "citations": [], "data": {}}, "proposal_id": None}

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
    router.register_app(MAIL_APP, agent="sales_profile", builder=_mail_builder(row["account_id"], row["email"]))
    yield row
    router.unregister_app(MAIL_APP)


async def test_a_thread_whose_resume_was_lost_continues_with_the_decision(app_client, personas, mail_app,
                                                                          monkeypatch):
    from corpai.agents import _runner

    await login(app_client, personas["sales_staff"].user_id)
    r = await run(app_client, "send", app=MAIL_APP)
    assert r["status"] == "waiting_approval"
    pid = r["pending_approval"]["proposal_id"]

    async def lost(*a, **kw):
        raise RuntimeError("resume failed")

    monkeypatch.setattr(_runner, "resume_for_proposal", lost)
    await login(app_client, personas["sales_head"].user_id)
    prop = next(p for p in (await app_client.get("/api/proposals?box=to_approve")).json() if p["proposal_id"] == pid)
    ok = await app_client.post(f"/api/proposals/{pid}/approve", json={"params_hash": prop["params_hash"],
                                                                     "reason": "fine"})
    assert ok.status_code == 200 and ok.json()["run"] is None
    monkeypatch.undo()
    await login(app_client, personas["sales_staff"].user_id)
    nxt = await app_client.post("/api/runs", json={"app": MAIL_APP, "input": "anything else?",
                                                   "thread_id": r["thread_id"]})
    assert nxt.status_code == 200, nxt.text
    assert nxt.json()["status"] == "done"


async def test_a_failed_execution_resumes_as_not_sent(app_client, personas, mail_app, monkeypatch):
    from corpai.proposals import service as service_mod

    await login(app_client, personas["sales_staff"].user_id)
    r = await run(app_client, "send", app=MAIL_APP)
    pid = r["pending_approval"]["proposal_id"]

    def broken(proposal_id: str):
        raise RuntimeError("owner connection lost")

    monkeypatch.setattr(service_mod, "execute_sync", broken)
    await login(app_client, personas["sales_head"].user_id)
    prop = next(p for p in (await app_client.get("/api/proposals?box=to_approve")).json() if p["proposal_id"] == pid)
    resp = await app_client.post(f"/api/proposals/{pid}/approve", json={"params_hash": prop["params_hash"],
                                                                       "reason": "fine"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposal"]["status"] == "failed" and body["run"]["status"] == "done"
    await login(app_client, personas["sales_staff"].user_id)
    trace = await app_client.get(f"/api/runs/{body['run']['run_id']}/trace")
    assert trace.status_code == 200
    approval = next(s for s in trace.json()["spans"] if s["name"] == "approval")
    assert approval["attrs"]["approved"] is False and approval["attrs"]["status"] == "failed"


# ---- the gazetteer follows the database ---------------------------------------------------------

@pytest.mark.security
async def test_new_names_from_another_process_reach_the_running_api(app_client, personas, fake_llm):
    """A contact added after the API started (synth re-run, another process) must classify as a
    person at once, so a question naming them never goes external."""
    from corpai.classify import classify_text

    name = "Ottilie Brandvoort"
    assert classify_text(f"Write to {name} about the pilot").level == 1
    owner_exec("INSERT INTO crm.contacts (contact_id, account_id, name, email, title, level, acl_groups)"
               " SELECT 'CT-99990', account_id, %s, 'ottilie.brandvoort@havenmeer-distributie.example', 'Buyer',"
               " level, acl_groups FROM crm.accounts WHERE account_id = 'A-0002'", (name,))
    try:
        await login(app_client, personas["eng_staff"].user_id)
        r = await run(app_client, f"Write to {name} about the pilot")
        assert r["route"] == "local" and r["hwm"] >= 3
        assert classify_text(f"Write to {name} about the pilot").level >= 3
    finally:
        owner_exec("DELETE FROM crm.contacts WHERE contact_id = 'CT-99990'")
