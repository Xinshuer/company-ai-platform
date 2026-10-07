"""Phase-B integration: the UI (ui/, B4) against the real API (B3) over the real MCP servers (B2)
and RAG (B1).

The UI's demo fixtures stand in for the API while it is opened from disk, so they must have the
shapes the API really returns. Each test below makes the real call through `app_client` and
compares it with the fixture for the same thing. It also checks the parts of the run payload the
UI renders (event types, the denied-row shape, L4 citations), that every error code the API can
emit has a UI string, and that the gazetteer is loaded when the API starts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from corpai.types import Level
from tests.api.support import login, owner_rows, run

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_Q = "What does FleetPilot do?"
ACCOUNT_Q = "Which of my customer accounts need attention?"


def _fixture(name: str) -> dict:
    text = (ROOT / "ui" / "fixtures" / f"{name}.js").read_text(encoding="utf-8")
    prefix = f"window.FIXTURES.{name} = "
    assert text.startswith(prefix), name
    return json.loads(text[len(prefix):].rstrip().rstrip(";"))


def _i18n() -> dict:
    text = (ROOT / "ui" / "js" / "i18n.js").read_text(encoding="utf-8")
    return json.loads(text[text.index("window.I18N = ") + len("window.I18N = "):].rstrip().rstrip(";"))


def _sample_runs() -> list[dict]:
    return [it["run"] for it in _fixture("runs")["items"]]


# ---- identity payloads -------------------------------------------------------------------------

async def test_personas_equal_the_fixture(app_client):
    r = await app_client.get("/api/personas")
    assert r.status_code == 200
    from corpai.synth.personas import PERSONAS

    # other tests in the session add throwaway personas (e.g. identity tests); compare the 14 real ones
    real = sorted((p for p in r.json() if p["persona_key"] in PERSONAS), key=lambda p: p["user_id"])
    fixture = sorted(_fixture("personas")["personas"], key=lambda p: p["user_id"])
    assert len(real) == len(PERSONAS) and real == fixture


async def test_me_equals_the_fixture_for_every_persona(app_client, personas):
    from corpai.agents import router

    fixture = _fixture("me")["by_persona"]
    assert set(fixture) == set(personas)
    for key, p in personas.items():
        await login(app_client, p.user_id)
        r = await app_client.get("/api/me")
        assert r.status_code == 200
        # The demo fixtures also show the milestone apps (recorded samples); the live API lists only
        # the apps whose graph exists today, in the same order and shape.
        expected = dict(fixture[key], apps=[a for a in fixture[key]["apps"] if router.app_ready(a["id"])])
        assert r.json() == expected, key


async def test_health_and_switches_have_the_fixture_keys(app_client, personas):
    h = (await app_client.get("/api/health")).json()
    fh = _fixture("health")
    assert set(h) == set(fh) and set(h["switches"]) == set(fh["switches"])
    await login(app_client, personas["platform_admin"].user_id)
    sw = (await app_client.get("/api/admin/switches")).json()
    assert set(sw) == set(_fixture("switches"))


# ---- runs and traces ---------------------------------------------------------------------------

def _check_event_shapes(events: list[dict], i18n: dict) -> None:
    for e in events:
        assert f"evt_{e['t']}" in i18n["zh"], f"the UI has no label for event type {e['t']!r}"
        if e["t"] == "tool":
            assert {"domain", "tool", "decision", "level", "denied"} <= set(e)
            assert all(isinstance(d, dict) and set(d) == {"id", "reason"} for d in e["denied"])
        elif e["t"] == "retrieval":
            assert {"count", "level", "collections"} <= set(e)
        elif e["t"] == "hwm":
            assert {"from", "to", "reason"} <= set(e) and e["to"] > e["from"]
        elif e["t"] == "route":
            assert {"route", "level", "reason"} <= set(e)
        elif e["t"] == "notice":
            assert {"code", "zh", "en"} <= set(e)
        elif e["t"] == "classify":
            assert {"level", "findings"} <= set(e)


async def test_run_payload_has_the_fixture_shape(app_client, personas):
    fixture_run = next(r for r in _sample_runs() if r["app"] == "qa" and r["status"] == "done")
    i18n = _i18n()
    await login(app_client, personas["sales_staff"].user_id)
    for q in (PUBLIC_Q, ACCOUNT_Q):
        r = await run(app_client, q)
        assert r["status"] == "done", r
        assert set(r) == set(fixture_run)
        assert set(r["output"]) == set(fixture_run["output"])
        for c in r["output"]["citations"]:
            assert set(c) == {"doc_id", "title", "level", "collection", "snippet"}
            if c["level"] >= Level.L4:
                assert c["snippet"] == ""  # the UI shows L4 citations as metadata only
        _check_event_shapes(r["events"], i18n)
    # the internal question used a CRM tool, so a tool event was checked above
    assert any(e["t"] == "tool" for e in r["events"])


async def test_local_down_run_has_the_fixture_error_shape(app_client, personas):
    from tests.api.support import set_switch_owner

    fixture_err = next(it["run"] for it in _fixture("runs")["items"]
                       if (it.get("switches") or {}).get("local_down") and it["run"]["status"] == "error")
    await login(app_client, personas["sales_staff"].user_id)
    set_switch_owner("local_down", True)
    try:
        r = await run(app_client, ACCOUNT_Q)
    finally:
        set_switch_owner("local_down", False)
    assert r["status"] == "error" and r["error"]["code"] == fixture_err["error"]["code"] == "local_unavailable"
    assert set(r) == set(fixture_err) and r["output"] == fixture_err["output"]
    assert r["route"] == "local"


async def test_trace_spans_have_the_fixture_keys(app_client, personas):
    fixture_span = next(iter(_fixture("traces")["by_run"].values()))["spans"][0]
    kinds = {k.removeprefix("kind_") for k in _i18n()["zh"] if k.startswith("kind_")}
    await login(app_client, personas["eng_staff"].user_id)
    r = await run(app_client, PUBLIC_Q)
    t = await app_client.get(f"/api/runs/{r['run_id']}/trace")
    assert t.status_code == 200
    spans = t.json()["spans"]
    assert spans and all(set(s) == set(fixture_span) for s in spans)
    assert {s["kind"] for s in spans} <= kinds


# ---- proposals and audit -----------------------------------------------------------------------

async def test_proposal_items_have_the_fixture_keys_before_and_after_a_decision(app_client, personas):
    from corpai import proposals

    staff, head = personas["sales_staff"], personas["sales_head"]
    fixture = _fixture("proposals")["by_persona"]
    pending_keys = set(fixture["sales_staff"]["mine"][0])
    decided = next(p for box in fixture.values() for lst in box.values() for p in lst if p.get("approver"))
    acc = owner_rows("SELECT account_id FROM crm.accounts WHERE owner_id = %s ORDER BY account_id LIMIT 1",
                     (staff.user_id,))[0]["account_id"]
    prop = await proposals.create(staff, agent="sales_profile", run_id=None, domain="crm", tool="log_interaction",
                                  params={"account_id": acc, "kind": "call", "summary": "Integration test call.",
                                          "contact_id": None, "sentiment": "neutral", "occurred_on": "2026-10-01"},
                                  summary="Log call (integration test)")

    await login(app_client, staff.user_id)
    mine = (await app_client.get("/api/proposals?box=mine")).json()
    item = next(p for p in mine if p["proposal_id"] == prop.id)
    assert pending_keys <= set(item)
    assert set(item["requester"]) == {"user_id", "name"} and item["requester"]["name"] == staff.name

    await login(app_client, head.user_id)
    todo = (await app_client.get("/api/proposals?box=to_approve")).json()
    item = next(p for p in todo if p["proposal_id"] == prop.id)
    r = await app_client.post(f"/api/proposals/{prop.id}/approve",
                              json={"params_hash": item["params_hash"], "reason": "integration test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"proposal", "run"}
    done = body["proposal"]
    assert set(decided) <= set(done)
    assert done["approver"] == {"user_id": head.user_id, "name": head.name}
    assert done["status"] in ("executed", "failed") and done["decided_at"] and done["reason"] == "integration test"


async def test_audit_entries_have_the_fixture_keys_and_the_chain_is_ok(app_client, personas):
    fixture = _fixture("audit")
    await login(app_client, personas["auditor"].user_id)
    r = await app_client.get("/api/audit?after_seq=0&limit=50")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == set(fixture)
    assert body["entries"] and all(set(fixture["entries"][0]) <= set(e) for e in body["entries"])
    assert set(body["chain"]) == set(fixture["chain"]) and body["chain"]["ok"] is True


# ---- errors ------------------------------------------------------------------------------------

def _api_error_codes() -> set[str]:
    codes: set[str] = set()
    for path in [*(ROOT / "corpai" / "api").glob("*.py"), ROOT / "corpai" / "agents" / "_runner.py",
                 ROOT / "corpai" / "agents" / "router.py"]:
        src = path.read_text(encoding="utf-8")
        codes |= set(re.findall(r'ApiError\(\d+, "([a-z_]+)"', src))
        codes |= set(re.findall(r'error_response\(\d+, "([a-z_]+)"', src))
        codes |= set(re.findall(r'^\s*code = "([a-z_]+)"', src, re.M))
        # _runner.error_of's table rows: (ge.LocalUnavailable, "local_unavailable", "message ...")
        codes |= set(re.findall(r'\((?:ge\.)?[A-Z]\w+, "([a-z_]+)",\s*\n?\s*"', src))
    from corpai.api import errors, routes_proposals

    codes |= set(errors._STATUS_CODES.values())
    codes |= {code for _status, code in routes_proposals._CODES.values()}
    return codes


def test_every_api_error_code_has_a_ui_title():
    codes = _api_error_codes()
    assert {"unauthenticated", "local_unavailable", "backend_error", "tool_unavailable"} <= codes  # the scan works
    i18n = _i18n()
    assert not sorted(c for c in codes if f"err_{c}" not in i18n["zh"] or f"err_{c}" not in i18n["en"])


async def test_unauthenticated_error_has_the_section_18_shape(app_client):
    app_client.cookies.clear()
    r = await app_client.get("/api/me")
    assert r.status_code == 401 and r.json() == {"error": {"code": "unauthenticated", "message": r.json()["error"]["message"]}}


# ---- startup -----------------------------------------------------------------------------------

async def test_api_startup_loads_the_gazetteer(app_client, personas):
    """D-09 / R-01: with the gazetteer loaded a contact's name classifies L3; empty, it would be L1
    and the gateway would fall back to forcing everything local."""
    from corpai.classify import classify_text

    name = owner_rows("SELECT name FROM crm.contacts ORDER BY contact_id LIMIT 1")[0]["name"]
    assert classify_text(f"Draft a follow-up email to {name}.").level >= Level.L3
