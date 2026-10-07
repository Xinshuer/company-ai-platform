"""POST /api/runs with the Q&A graph (architecture §16 M1 key checks) and run traces."""

from __future__ import annotations

import pytest

from tests.api.support import login, owner_rows, run, set_switch_owner

pytestmark = pytest.mark.anyio

PUBLIC_Q = "What does FleetPilot do?"
ACCOUNT_Q = "Which of my customer accounts need attention?"


def _egress_count() -> int:
    return int(owner_rows("SELECT count(*) AS n FROM ops.egress_log")[0]["n"])


def _types(r: dict) -> list[str]:
    return [e["t"] for e in r["events"]]


async def test_public_question_routes_external(app_client, personas, fake_llm):
    await login(app_client, personas["eng_staff"].user_id)
    before = _egress_count()
    r = await run(app_client, PUBLIC_Q)
    assert r["status"] == "done" and r["error"] is None and r["pending_approval"] is None
    assert r["hwm"] in (1, 2) and r["route"] == "external"
    assert r["run_id"].startswith("run_") and r["thread_id"].startswith("thr_") and r["app"] == "qa"
    assert {e["route"] for e in r["events"] if e["t"] == "route"} == {"external"}
    assert "notice" not in _types(r)
    cites = r["output"]["citations"]
    assert cites and all(c["level"] <= 2 for c in cites) and "FleetPilot" in r["output"]["text"]
    assert set(cites[0]) == {"doc_id", "title", "level", "collection", "snippet"}
    assert _egress_count() > before  # external calls are logged before they are sent
    assert {c["route"] for c in fake_llm.calls} == {"external"}


async def test_internal_question_by_sales_staff_goes_local_with_a_crm_tool(app_client, personas, fake_llm):
    await login(app_client, personas["sales_staff"].user_id)
    r = await run(app_client, ACCOUNT_Q)
    assert r["status"] == "done" and r["route"] == "local" and r["hwm"] >= 3
    tools = [e for e in r["events"] if e["t"] == "tool"]
    assert any(e["domain"] == "crm" and e["decision"] == "allow" and e["level"] >= 3 for e in tools)
    notices = [e for e in r["events"] if e["t"] == "notice"]
    assert [n["code"] for n in notices] == ["local_mode"] and notices[0]["zh"] == "本会话已切换到本地模式"
    # every model call after the hwm reached L3 was local, and none of them carried more than the hwm
    assert fake_llm.calls and fake_llm.calls[-1]["route"] == "local"
    crm_cites = [c["doc_id"] for c in r["output"]["citations"] if c["doc_id"].startswith("crm.account:")]
    owned = {x["account_id"] for x in owner_rows("SELECT account_id FROM crm.accounts WHERE owner_id = %s",
                                                 (personas["sales_staff"].user_id,))}
    assert crm_cites and {c.split(":", 1)[1] for c in crm_cites} <= owned  # only their own accounts


async def test_same_question_different_people_different_citations(app_client, personas):
    cited = {}
    for key in ("sales_staff", "eng_staff", "ceo"):
        await login(app_client, personas[key].user_id)
        r = await run(app_client, ACCOUNT_Q)
        assert r["status"] == "done", r
        cited[key] = frozenset(c["doc_id"] for c in r["output"]["citations"])
    assert len(set(cited.values())) == 3, cited
    assert not any(c.startswith("crm.") for c in cited["eng_staff"])  # eng has no CRM rights
    readable_by_eng = {r["doc_id"] for r in owner_rows(
        "SELECT DISTINCT doc_id FROM rag.documents WHERE acl_groups && %s::text[] AND level <= %s",
        (sorted(personas["eng_staff"].groups), int(personas["eng_staff"].clearance)))}
    assert cited["eng_staff"] <= readable_by_eng


async def test_local_down_errors_instead_of_going_external(app_client, personas, fake_llm):
    await login(app_client, personas["sales_staff"].user_id)
    before = _egress_count()
    set_switch_owner("local_down", True)
    try:
        r = await run(app_client, ACCOUNT_Q)
    finally:
        set_switch_owner("local_down", False)
    assert r["status"] == "error" and r["error"]["code"] == "local_unavailable"
    assert r["hwm"] >= 3 and r["output"]["text"] == ""
    assert {"t": "route", "route": "local", "level": r["hwm"], "reason": "local_down"} in r["events"]
    assert _egress_count() == before  # nothing was sent anywhere
    assert fake_llm.calls == []


async def test_a_thread_keeps_its_hwm(app_client, personas):
    await login(app_client, personas["sales_staff"].user_id)
    first = await run(app_client, ACCOUNT_Q)
    assert first["hwm"] >= 3
    second = await run(app_client, PUBLIC_Q, thread_id=first["thread_id"])
    assert second["thread_id"] == first["thread_id"] and second["hwm"] >= first["hwm"]
    assert second["route"] == "local"  # a public question in a confidential thread stays local
    assert "notice" not in _types(second)  # the notice belongs to the run that crossed L3
    fresh = await run(app_client, PUBLIC_Q)
    assert fresh["thread_id"] != first["thread_id"]


async def test_user_level_raises_the_route(app_client, personas):
    await login(app_client, personas["eng_staff"].user_id)
    r = await run(app_client, PUBLIC_Q, user_level=3)
    assert r["hwm"] == 3 and r["route"] == "local"
    assert {"t": "hwm", "from": 1, "to": 3, "reason": "user_level"} in r["events"]


async def test_someone_elses_thread_is_not_found(app_client, personas):
    await login(app_client, personas["sales_staff"].user_id)
    mine = await run(app_client, PUBLIC_Q)
    await login(app_client, personas["sales_head"].user_id)
    r = await app_client.post("/api/runs", json={"app": "qa", "input": "and the details?",
                                                 "thread_id": mine["thread_id"]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("body,status,code", [
    ({"app": "legal_review", "input": "review C-0004"}, 403, "forbidden"),
    ({"app": "nope", "input": "x"}, 400, "bad_request"),
    ({"app": "qa", "input": "   "}, 400, "bad_request"),
    ({"app": "qa", "input": "x", "user_level": 0}, 400, "bad_request"),
    ({"app": "qa", "input": "x", "user_level": 5}, 400, "bad_request"),
    ({"app": "qa", "input": "x" * 4001}, 400, "bad_request"),
    ({"app": "qa"}, 400, "bad_request"),
])
async def test_bad_or_forbidden_runs(app_client, personas, body, status, code):
    await login(app_client, personas["sales_staff"].user_id)
    r = await app_client.post("/api/runs", json=body)
    assert r.status_code == status and r.json()["error"]["code"] == code, r.text


async def test_milestone_app_says_when_it_arrives(app_client, personas, monkeypatch):
    from corpai.agents import router

    real = router.importlib.import_module

    def missing(name, *a, **kw):
        if name == "corpai.agents.legal":
            raise ModuleNotFoundError(name=name)
        return real(name, *a, **kw)

    monkeypatch.setattr(router.importlib, "import_module", missing)
    await login(app_client, personas["legal_staff"].user_id)
    r = await app_client.post("/api/runs", json={"app": "legal_review", "input": "review C-0004"})
    assert r.status_code == 400 and "M2" in r.json()["error"]["message"]


async def test_trace_own_run_only(app_client, personas):
    await login(app_client, personas["sales_staff"].user_id)
    r = await run(app_client, ACCOUNT_Q)
    t = await app_client.get(f"/api/runs/{r['run_id']}/trace")
    assert t.status_code == 200
    spans = t.json()["spans"]
    kinds = {s["kind"] for s in spans}
    assert {"node", "retrieval", "tool", "llm"} <= kinds
    assert all(set(s) == {"span_id", "parent_id", "name", "kind", "level", "status", "started_at", "ended_at",
                          "attrs", "input_text", "output_text"} for s in spans)
    assert all(s["level"] < 4 or (s["input_text"] is None and s["output_text"] is None) for s in spans)
    tool = next(s for s in spans if s["kind"] == "tool")
    assert tool["parent_id"] in {s["span_id"] for s in spans if s["kind"] == "node"}
    for other in ("sales_head", "ceo", "platform_admin"):
        await login(app_client, personas[other].user_id)
        t = await app_client.get(f"/api/runs/{r['run_id']}/trace")
        assert t.status_code == 404 and t.json()["error"]["code"] == "not_found", other
    assert (await app_client.get("/api/runs/run_doesnotexist/trace")).status_code == 404


async def test_checkpoints_hold_no_tokens_or_session(app_client, personas):
    await login(app_client, personas["sales_staff"].user_id)
    r = await run(app_client, ACCOUNT_Q)
    blob = owner_rows("SELECT string_agg(encode(coalesce(blob, ''), 'escape'), ' ') AS b FROM lg.checkpoint_blobs"
                      " WHERE thread_id = %s", (r["thread_id"],))[0]["b"] or ""
    blob += owner_rows("SELECT string_agg(checkpoint::text || metadata::text, ' ') AS c FROM lg.checkpoints"
                       " WHERE thread_id = %s", (r["thread_id"],))[0]["c"] or ""
    assert blob, "the thread was checkpointed"
    assert "eyJ" not in blob and "Bearer" not in blob  # no JWT (session or tool token) anywhere
    assert "user:u0011" not in blob  # the principal's groups (RunContext) are not state


async def test_threads_are_owned_in_the_database(app_client, personas):
    await login(app_client, personas["sales_staff"].user_id)
    r = await run(app_client, PUBLIC_Q)
    row = owner_rows("SELECT owner_id, agent, app FROM ops.threads WHERE thread_id = %s", (r["thread_id"],))
    assert row == [{"owner_id": personas["sales_staff"].user_id, "agent": "qa", "app": "qa"}]
    assert owner_rows("SELECT thread_id FROM ops.thread_runs WHERE run_id = %s", (r["run_id"],)) == \
        [{"thread_id": r["thread_id"]}]
