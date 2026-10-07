"""Canary test (design-review 二.2 "CI 里加金丝雀测试", architecture §15 canaries).

Six seed-derived KB-CANARY strings sit in L4 rows and documents (synth.canaries, owner-only). Every
app the router reports as ready is run through the API by the personas allowed to start it, with
representative inputs and with inputs built from the canary hosts themselves, asked by the people
who may read those hosts, so the canaries really enter sessions. Then, read back as the owner:

- no external egress row (ops.egress_log: everything sent to the external route, logged before it
  is sent) contains a canary, and none classifies at L3 or above;
- no model call on the external route carried a canary (the fake backend's own record, which
  would also catch a path around the egress log);
- trace text and audit details of those runs hold no canary (L4 text is metadata only);
- and the test is not vacuous: canaries did reach the local model.
"""

from __future__ import annotations

import json

import pytest

from corpai.classify import classify_text, load_gazetteer_from_db
from corpai.types import Level
from tests.api.support import owner_rows
from tests.security import _apps

pytestmark = [pytest.mark.anyio, pytest.mark.security]

MAX_READERS = 2  # people per canary host and app; enough to pull it in, few enough to stay fast
PUBLIC_FOLLOW_UP = "What does FleetPilot do?"


def _host_hints(location: str) -> dict:
    """What a person would type to reach a canary host: a title, a contract id, a name."""
    kind, _, rest = location.partition(":")
    if kind == "file":
        doc_id = rest.rsplit("/", 1)[-1].removesuffix(".md")
        row = owner_rows("SELECT title FROM rag.documents WHERE doc_id = %s AND status = 'current'", (doc_id,))
        hints = {"title": row[0]["title"] if row else doc_id}
        if doc_id.startswith("C-"):
            hints["contract_id"] = doc_id
        return hints
    table, _, key = rest.partition(":")
    if table == "legal.contracts":
        return {"contract_id": key, "title": f"contract {key}"}
    if table == "crm.interactions":
        row = owner_rows("SELECT a.name FROM crm.interactions i JOIN crm.accounts a USING (account_id) "
                         "WHERE i.interaction_id = %s", (key,))
        return {"account": row[0]["name"]} if row else {}
    if table == "hr.performance":
        row = owner_rows("SELECT name FROM core.users WHERE user_id = %s", (key.partition(":")[0],))
        return {"person": row[0]["name"]} if row else {}
    return {}


def _canary_inputs(app: str, hints: dict) -> list[str]:
    if app == "qa":
        out = []
        if "title" in hints:
            out.append(f"What does '{hints['title']}' say? Quote any reference numbers.")
        if "account" in hints:
            out.append(f"What were our latest customer interactions with {hints['account']}?")
        if "person" in hints:
            out.append(f"How was {hints['person']} rated in the 2026-H1 performance review?")
        return out
    if app == "legal_review":
        return [hints["contract_id"]] if "contract_id" in hints else []
    if app == "sales_profile":
        return [hints["account"]] if "account" in hints else []
    if app == "report":
        return []  # its input only names a period; the static input already reads every domain
    # an app without its own rule gets the plain hint, which is what a user would type
    return [v for k, v in hints.items() if k in ("contract_id", "account", "person")][:1]


def egress_problems(rows: list[dict], strings: list[str], public_ids: set[int]) -> list[str]:
    """Why each external egress row should not exist: a canary in it, a level above L2, or a
    payload that classifies at L3+ (published-content calls are classified as published)."""
    out = []
    for row in rows:
        if any(s in row["payload"] for s in strings):
            out.append(f"{row['id']}: canary")
        if row["route"] != "external" or int(row["level"]) > Level.L2:
            out.append(f"{row['id']}: logged as {row['route']} L{row['level']}")
        level = classify_text(_apps.payload_message_text(row["payload"]), public=row["id"] in public_ids).level
        if level >= Level.L3:
            out.append(f"{row['id']} (run {row['run_id']}): payload classifies as L{int(level)}")
    return out


def _external_above_l2(run: dict) -> list[dict]:
    return [e for e in run.get("events") or [] if e.get("t") == "route" and e.get("route") == "external"
            and int(e.get("level") or 4) > Level.L2]


@pytest.mark.parametrize("app", _apps.all_apps())
async def test_no_canary_leaves_through_the_external_route(app, app_client, personas, fake_llm):
    if not _apps.router.app_ready(app):
        pytest.skip(f"{app}: graph not built yet (router.app_ready is False)")
    load_gazetteer_from_db()  # the classifier below must know the names the gateway knew
    starters = _apps.starters(app, personas)
    assert starters, f"nobody may start {app}"
    canaries = _apps.canaries()
    strings = sorted({c["canary"] for c in canaries})
    assert len(strings) == 6

    egress_before = _apps.max_id("ops.egress_log", "id")
    plan: list[tuple[str, str, bool]] = [(k, text, False) for k in starters for text in _apps.inputs_for(app)]
    for c in canaries:
        readers = [k for k in _apps.readers_of(c["location"], personas) if k in starters][:MAX_READERS]
        for text in _canary_inputs(app, _host_hints(c["location"])):
            plan += [(k, text, True) for k in readers]

    runs: list[dict] = []
    for key, text, pulls_canary in plan:
        await _apps.as_persona(app_client, personas, key)
        run = await _apps.post_run(app_client, app, text)
        runs.append({**run, "persona": key, "input": text})
        if pulls_canary and app == "qa" and run.get("thread_id"):
            # a public question in the thread that just held a canary must stay local
            nxt = await _apps.post_run(app_client, app, PUBLIC_FOLLOW_UP, thread_id=run["thread_id"])
            runs.append({**nxt, "persona": key, "input": PUBLIC_FOLLOW_UP})
            if run.get("hwm", 0) >= Level.L3:
                assert nxt.get("route") in (None, "local"), (key, text, nxt.get("route"))

    assert all(r["http"] in (200, 400, 403) for r in runs), [(r["persona"], r["input"], r["http"]) for r in runs]
    assert any(r["http"] == 200 and r["status"] == "done" for r in runs), f"no {app} run completed"
    assert not [r for r in runs if _external_above_l2(r)]

    # 1. the egress log: what actually left, written before sending
    assert egress_problems(_apps.egress_after(egress_before), strings, _apps.public_only_egress()) == []

    # 2. the backend's own record of every call
    def carries(call: dict) -> set[str]:
        blob = json.dumps(call["messages"], ensure_ascii=False) + json.dumps(call.get("tools") or [])
        return {s for s in strings if s in blob}

    external = [c for c in fake_llm.calls if c["route"] == "external"]
    assert not [c["purpose"] for c in external if carries(c)]
    assert all(c["level"] <= Level.L2 for c in external)
    if app == "qa":
        # not vacuous: the canary documents were retrieved by their readers and reached the local model
        seen = set().union(*(carries(c) for c in fake_llm.calls if c["route"] == "local"))
        assert len(seen) >= 3, f"only {len(seen)} canaries reached a session: the test proves little"

    # 3. traces and audit of these runs: L4 text is never stored
    run_ids = sorted({r["run_id"] for r in runs if r.get("run_id")})
    spans = owner_rows("SELECT coalesce(input_text, '') || ' ' || coalesce(output_text, '') || ' ' || attrs::text "
                       "AS t FROM trace.spans WHERE run_id = ANY(%s)", (run_ids,))
    assert spans and not [s for s in spans if any(c in s["t"] for c in strings)]
    audit = _apps.audit_for_runs(run_ids)
    assert audit and not [a for a in audit if any(c in json.dumps(a["details"] or {}) for c in strings)]


def _row(i: int, text: str, *, level: int = 1) -> dict:
    payload = json.dumps({"messages": [{"role": "user", "content": text}], "model": "m"})
    return {"id": i, "route": "external", "level": level, "run_id": f"run_{i}", "payload": payload}


def test_the_egress_check_catches_leaks(monkeypatch):
    """The scanner itself: a canary, an e-mail address, internal money and a mislabelled row are each
    caught; public text and published company facts (public_only) are not."""
    from corpai.classify import names, set_gazetteer

    # the gazetteer is process-wide: put the session's back afterwards
    monkeypatch.setattr(names, "_state", names._state)
    set_gazetteer(["Jonas Becker"])
    canary = "KB-CANARY-0A1B2C3D"
    rows = [_row(1, "What does FleetPilot do?"),
            _row(2, f"Summarise file reference {canary}"),
            _row(3, "Write to Jonas Becker at jonas.becker@nordhafen-logistik.example"),
            _row(4, "Our quote to Havenmeer was EUR 412,000 with a 20% discount"),
            _row(5, "Elbstrom is family-owned and reports annual revenue of about EUR 120,000,000"),
            _row(6, "Elbstrom is family-owned and reports annual revenue of about EUR 120,000,000"),
            _row(7, "hello", level=3)]
    problems = egress_problems(rows, [canary], public_ids={5})
    flagged = sorted({p.split(":")[0].split(" ")[0] for p in problems})
    assert flagged == ["2", "3", "4", "6", "7"], problems
