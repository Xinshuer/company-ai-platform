"""Chaos tests: the outage switches across every ready app (design-review 二.2, 二.9, 四.2; §8 rule 4).

- local_down: a batch of L3/L4 requests (user level 3 and 4, and content that is L3 by itself)
  across the ready apps. Every run that needed the model ends with local_unavailable, and the batch
  adds zero external egress rows: confidential work never falls back to the external route.
- local_down: L1/L2 requests still work, on the external route.
- external_down: an L1 request goes local, audited as external_down_fallback (the safe direction),
  and nothing leaves; with both routes down it fails closed.

The switches are flipped as the owner and always put back.
"""

from __future__ import annotations

import pytest

from corpai.types import Level
from tests.api.support import set_switch_owner
from tests.security import _apps

pytestmark = [pytest.mark.anyio, pytest.mark.security]

PUBLIC_Q = "What does FleetPilot do?"
# L3 by content alone (an e-mail address), whatever the user level says
CONTENT_L3_Q = "Draft a reply to anna.k@nordhafen-logistik.example about the overdue invoices."
PERSONAS_PER_APP = 2
# app -> agent whose egress is tolerated as a known gap. Empty: the sales_web extraction now runs
# from the user's level (issues-log WI); keep the mechanism for a future, documented gap only.
KNOWN_USER_LEVEL_GAP: dict[str, str] = {}


@pytest.fixture
def switches():
    """switches(local_down=..., external_down=...) for the test; both off again afterwards."""
    def _set(*, local_down: bool = False, external_down: bool = False) -> None:
        set_switch_owner("local_down", local_down)
        set_switch_owner("external_down", external_down)
    try:
        yield _set
    finally:
        _set()


def _model_calls(audit: list[dict], run_id: str) -> list[dict]:
    return [a for a in audit if a["run_id"] == run_id and a["event"] == "model_call"]


async def _batch(app_client, personas, apps: list[str], user_levels: tuple[int, ...]) -> list[dict]:
    runs = []
    for app in apps:
        for key in _apps.starters(app, personas)[:PERSONAS_PER_APP]:
            await _apps.as_persona(app_client, personas, key)
            for text in _apps.inputs_for(app):
                for lvl in user_levels:
                    run = await _apps.post_run(app_client, app, text, user_level=lvl)
                    runs.append({**run, "app": app, "persona": key, "input": text, "user_level": lvl})
    return runs


def _egress_agents(rows: list[dict], audit: list[dict]) -> set[str]:
    """The agents whose model calls wrote these egress rows (audit model_call details.egress_id)."""
    ids = {r["id"] for r in rows}
    return {a for c in audit if c["event"] == "model_call" and (c["details"] or {}).get("egress_id") in ids
            for a in (x.removeprefix("agent:") for x in c["act_chain"] or [])}


@pytest.mark.parametrize("app", _apps.all_apps())
async def test_local_down_confidential_requests_fail_closed(app, app_client, personas, fake_llm, switches):
    if not _apps.router.app_ready(app):
        pytest.skip(f"{app}: graph not built yet (router.app_ready is False)")
    egress_before = _apps.max_id("ops.egress_log", "id")
    switches(local_down=True)
    runs = await _batch(app_client, personas, [app], (int(Level.L3), int(Level.L4)))
    if app == "qa":
        for key in ("sales_staff", "eng_staff"):
            await _apps.as_persona(app_client, personas, key)
            run = await _apps.post_run(app_client, "qa", CONTENT_L3_Q)
            runs.append({**run, "app": "qa", "persona": key, "input": CONTENT_L3_Q, "user_level": 1})
    switches()

    assert runs, f"nobody ran {app}"
    audit = _apps.audit_for_runs(r["run_id"] for r in runs)
    egress = _apps.egress_after(egress_before)
    known = KNOWN_USER_LEVEL_GAP.get(app)
    if egress and known and _egress_agents(egress, audit) == {known} and all(e["level"] == Level.L1 for e in egress):
        pytest.xfail(f"{app}: {KNOWN_USER_LEVEL_GAP[app]} ignores the user's raised level (change request)")
    by_run = {r["run_id"]: (r["persona"], r["input"], r["user_level"]) for r in runs}
    leaked = [(by_run.get(e["run_id"]), e["level"]) for e in egress]
    assert leaked == [], f"L3/L4 requests sent something to the external route: {leaked}"
    assert not [c for c in fake_llm.calls if c["route"] == "external"]
    attempted = 0
    for r in runs:
        where = (r["app"], r["persona"], r["input"], r["user_level"])
        assert r["http"] in (200, 400, 403), where
        assert r.get("route") != "external", where
        assert not [e for e in r["events"] if e.get("t") == "route" and e.get("route") == "external"], where
        calls = _model_calls(audit, r["run_id"]) if r.get("run_id") else []
        if calls:
            attempted += 1
            # every model call of the run was refused for local_down, none was re-routed
            assert {(c["decision"], c["reason"], c["route"]) for c in calls} == {("deny", "local_down", "local")}, \
                (where, calls)
            assert r["status"] == "error" and r["error"]["code"] == "local_unavailable", (where, r.get("error"))
            assert r["output"]["text"] == "", where
    if app == "qa":
        # the Q&A graph always needs the model: every run ended local_unavailable, at the raised level
        assert all(r["status"] == "error" and r["error"]["code"] == "local_unavailable" for r in runs)
        assert all(r["hwm"] >= max(int(Level.L3), r["user_level"]) for r in runs)
        assert attempted == len(runs)


async def test_local_down_public_requests_still_work(app_client, personas, fake_llm, switches):
    await _apps.as_persona(app_client, personas, "eng_staff")
    egress_before = _apps.max_id("ops.egress_log", "id")
    switches(local_down=True)
    run = await _apps.post_run(app_client, "qa", PUBLIC_Q)
    switches()
    assert run["status"] == "done" and run["route"] == "external" and run["hwm"] <= Level.L2, run.get("error")
    rows = _apps.egress_after(egress_before)
    assert rows and all(r["level"] <= Level.L2 and r["run_id"] == run["run_id"] for r in rows)
    assert {c["route"] for c in fake_llm.calls} == {"external"}


async def test_external_down_public_request_goes_local_and_is_audited(app_client, personas, fake_llm, switches):
    await _apps.as_persona(app_client, personas, "eng_staff")
    egress_before = _apps.max_id("ops.egress_log", "id")
    switches(external_down=True)
    run = await _apps.post_run(app_client, "qa", PUBLIC_Q)
    switches()
    assert run["status"] == "done" and run["route"] == "local" and run["hwm"] <= Level.L2
    routes = [e for e in run["events"] if e["t"] == "route"]
    assert routes == [{"t": "route", "route": "local", "level": run["hwm"], "reason": "external_down_fallback"}]
    assert _apps.egress_after(egress_before) == []
    assert fake_llm.calls and {c["route"] for c in fake_llm.calls} == {"local"}
    audit = _apps.audit_for_runs([run["run_id"]])
    reroutes = [a for a in audit if a["event"] == "route_decision"]
    calls = _model_calls(audit, run["run_id"])
    assert calls and len(reroutes) == len(calls)  # one reroute record per model call
    assert {(a["decision"], a["reason"], a["route"]) for a in reroutes} == {("reroute", "external_down_fallback",
                                                                             "local")}
    assert {(c["decision"], c["reason"], c["route"]) for c in calls} == {("allow", "external_down_fallback", "local")}


async def test_external_down_no_app_sends_anything_out(app_client, personas, fake_llm, switches):
    """With the external route off, nothing in any ready app reaches it, public work included."""
    egress_before = _apps.max_id("ops.egress_log", "id")
    switches(external_down=True)
    runs = await _batch(app_client, personas, _apps.ready_apps(), (int(Level.L1),))
    switches()
    assert _apps.egress_after(egress_before) == []
    assert not [c for c in fake_llm.calls if c["route"] == "external"]
    assert all(r["http"] in (200, 400, 403) for r in runs)
    audit = _apps.audit_for_runs(r["run_id"] for r in runs)
    assert not [a for a in audit if a["event"] == "model_call" and a["route"] == "external"]


async def test_both_routes_down_fails_closed(app_client, personas, fake_llm, switches):
    await _apps.as_persona(app_client, personas, "eng_staff")
    egress_before = _apps.max_id("ops.egress_log", "id")
    switches(local_down=True, external_down=True)
    run = await _apps.post_run(app_client, "qa", PUBLIC_Q)
    switches()
    assert run["status"] == "error" and run["error"]["code"] == "local_unavailable"
    assert _apps.egress_after(egress_before) == [] and fake_llm.calls == []
