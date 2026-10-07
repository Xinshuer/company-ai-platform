"""Audit log (auditor only, reads audited), outage switches (platform admin only), trace statistics
with a minimum group size, and checkpoint pruning."""

from __future__ import annotations

from datetime import timedelta

import pytest

from corpai import clock
from tests.api.support import login, owner_exec, owner_rows, run

pytestmark = pytest.mark.anyio


def _audit_reads(actor: str, decision: str) -> int:
    return int(owner_rows("SELECT count(*) AS n FROM audit.log WHERE event = 'audit_read' AND actor = %s"
                          " AND decision = %s", (actor, decision))[0]["n"])


@pytest.mark.parametrize("key", ["sales_staff", "ceo", "platform_admin"])
async def test_audit_log_is_for_the_auditor_only(app_client, personas, key):
    p = personas[key]
    await login(app_client, p.user_id)
    before = _audit_reads(p.user_id, "deny")
    r = await app_client.get("/api/audit")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert _audit_reads(p.user_id, "deny") == before + 1  # attempts are on record too


async def test_auditor_reads_the_log_and_the_read_is_audited(app_client, personas):
    a = personas["auditor"]
    await login(app_client, a.user_id)
    before = _audit_reads(a.user_id, "allow")
    r = await app_client.get("/api/audit?after_seq=0&limit=1000")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chain"]["ok"] is True and body["chain"]["checked"] >= len(body["entries"]) > 0
    e = body["entries"][-1]
    assert {"seq", "ts", "actor", "act_chain", "event", "action", "resource", "decision", "reason", "route",
            "level", "approval_id", "run_id", "details"} <= set(e)
    assert _audit_reads(a.user_id, "allow") == before + 1
    last = body["entries"][-1]["seq"]
    r2 = (await app_client.get(f"/api/audit?after_seq={last - 1}&limit=5")).json()
    assert any(x["event"] == "audit_read" and x["actor"] == a.user_id for x in r2["entries"])
    assert (await app_client.get("/api/audit?limit=5000")).status_code == 400


async def test_admin_switches_are_for_the_platform_admin(app_client, personas):
    for key in ("sales_staff", "auditor", "ceo"):
        await login(app_client, personas[key].user_id)
        assert (await app_client.get("/api/admin/switches")).status_code == 403
        r = await app_client.post("/api/admin/switches", json={"name": "local_down", "on": True, "reason": "drill"})
        assert r.status_code == 403
        r = await app_client.post("/api/admin/switches", json={"name": "bogus", "on": True, "reason": "drill"})
        assert r.status_code == 403  # a non-admin learns nothing about valid input
    assert owner_rows("SELECT \"on\" FROM ops.switches WHERE name = 'local_down'")[0]["on"] is False

    admin = personas["platform_admin"]
    await login(app_client, admin.user_id)
    assert (await app_client.get("/api/admin/switches")).json() == {"local_down": False, "external_down": False}
    try:
        r = await app_client.post("/api/admin/switches", json={"name": "external_down", "on": True, "reason": "drill"})
        assert r.status_code == 200 and r.json() == {"local_down": False, "external_down": True}
        assert (await app_client.get("/api/health")).json()["switches"]["external_down"] is True
        rows = owner_rows("SELECT decision, reason FROM audit.log WHERE event = 'outage_switch' AND actor = %s"
                          " ORDER BY seq DESC LIMIT 1", (admin.user_id,))
        assert rows == [{"decision": "allow", "reason": "drill"}]
        bad = await app_client.post("/api/admin/switches", json={"name": "bogus", "on": True, "reason": "x"})
        assert bad.status_code == 400
        no_reason = await app_client.post("/api/admin/switches", json={"name": "local_down", "on": True, "reason": ""})
        assert no_reason.status_code == 400
    finally:
        await app_client.post("/api/admin/switches", json={"name": "external_down", "on": False, "reason": "end drill"})
    assert owner_rows("SELECT \"on\" FROM ops.switches WHERE name = 'external_down'")[0]["on"] is False


async def test_external_down_moves_public_questions_local(app_client, personas):
    from tests.api.support import set_switch_owner

    await login(app_client, personas["eng_staff"].user_id)
    set_switch_owner("external_down", True)
    try:
        r = await run(app_client, "What does FleetPilot do?")
    finally:
        set_switch_owner("external_down", False)
    assert r["status"] == "done" and r["route"] == "local" and r["hwm"] <= 2
    assert any(e["t"] == "route" and e["reason"] == "external_down_fallback" for e in r["events"])


async def test_dept_stats_hides_small_groups(pg, personas):
    from corpai.ids import new_id
    from corpai.trace import Tracer, dept_stats
    from corpai.types import Level

    since = clock.now_utc() - timedelta(seconds=1)
    tracer = Tracer()
    legal = [p for k, p in personas.items() if str(p.dept) == "legal"]
    # four distinct legal people with plenty of spans: still too small a group to show
    for p in legal[:2]:
        for _ in range(4):
            async with tracer.span("probe", kind="policy", run_id=new_id("run"), principal=p, level=Level.L1):
                pass
    assert await dept_stats(personas["legal_head"], since) == []
    # sales: five distinct people, one span each -> shown
    sales_ids = [r["user_id"] for r in owner_rows("SELECT user_id FROM core.users WHERE dept = 'sales' AND active"
                                                  " ORDER BY user_id LIMIT 5")]
    from corpai.identity import directory

    for uid in sales_ids:
        p = directory.get_principal_sync(uid)
        async with tracer.span("probe", kind="policy", run_id=new_id("run"), principal=p, level=Level.L1):
            pass
    rows = await dept_stats(personas["sales_head"], since)
    assert any(r["kind"] == "policy" and r["n"] >= 5 for r in rows)


async def test_prune_deletes_idle_threads(app_client, personas):
    from corpai.agents._checkpoint import prune_sync
    from corpai.cli import prune as prune_cli

    await login(app_client, personas["sales_staff"].user_id)
    old = await run(app_client, "What does FleetPilot do?")
    new = await run(app_client, "What does FleetPilot do?")
    # age the first thread: its checkpoints carry an ISO timestamp in checkpoint->>'ts'
    owner_exec("UPDATE lg.checkpoints SET checkpoint = jsonb_set(checkpoint, '{ts}', to_jsonb(%s::text))"
               " WHERE thread_id = %s", ((clock.now_utc() - timedelta(days=40)).isoformat(), old["thread_id"]))
    counts = prune_sync(30)
    assert counts["pruned_threads"] >= 1 and counts["checkpoints"] >= 1
    assert owner_rows("SELECT count(*) AS n FROM lg.checkpoints WHERE thread_id = %s", (old["thread_id"],))[0]["n"] == 0
    assert owner_rows("SELECT count(*) AS n FROM ops.threads WHERE thread_id = %s", (old["thread_id"],))[0]["n"] == 0
    assert owner_rows("SELECT count(*) AS n FROM lg.checkpoints WHERE thread_id = %s", (new["thread_id"],))[0]["n"] > 0
    # a pruned thread cannot be continued with a reset hwm
    r = await app_client.post("/api/runs", json={"app": "qa", "input": "more", "thread_id": old["thread_id"]})
    assert r.status_code == 404
    with pytest.raises(ValueError):
        prune_sync(0)
    assert prune_cli.main(["--days", "30"]) == 0
    assert owner_rows("SELECT count(*) AS n FROM audit.log WHERE event = 'owner_job' AND actor = 'svc:prune'")[0]["n"] >= 1
