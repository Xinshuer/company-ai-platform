"""The report app and the metric endpoints through HTTP (§18 errors, policy, audit).

- POST /api/runs {app: report} runs the graph as the logged-in person; who may start it is policy's.
- Export: the file comes from the caller's own run, carries the watermark (user id, time, run, level),
  verifies, and is audited with metadata only; someone else's run is a 404, a person without the right
  is refused.
- Metrics: sign-off and publication by the head of finance only (refusals audited, the SQL check
  behind it), drafts visible to finance and the CEO only, published rows to their audience, platform
  roles refused; refresh is the CFO's demo button.
"""

from __future__ import annotations

import pytest

from corpai.metrics import verify_export
from tests.api.support import login, owner_rows, run, set_switch_owner
from tests.m3.conftest import API_PERIOD, REPORT_INPUT, REPORT_PERIOD

pytestmark = pytest.mark.anyio


def _seq() -> int:
    return owner_rows("SELECT coalesce(max(seq), 0) AS s FROM audit.log")[0]["s"]


def _audit(after: int, event: str) -> list[dict]:
    return owner_rows("SELECT * FROM audit.log WHERE seq > %s AND event = %s ORDER BY seq", (after, event))


async def _as(client, personas, key: str):
    await login(client, personas[key].user_id)
    return personas[key]


def _err(r, status: int, code: str) -> None:
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code and r.json()["error"]["message"]


# ---- report runs and export ---------------------------------------------------------------------

async def test_ceo_report_run_and_watermarked_export(app_client, personas, q2):
    ceo = await _as(app_client, personas, "ceo")
    out = await run(app_client, REPORT_INPUT, app="report")
    assert out["status"] == "done" and out["route"] == "local" and out["hwm"] >= 3
    data = out["output"]["data"]
    assert data["period"] == REPORT_PERIOD and data["export"] == {"domain": "company", "allowed": True}
    assert "A-0001" in out["output"]["text"] and "{metric:" not in out["output"]["text"]

    before = _seq()
    r = await app_client.post(f"/api/reports/{out['run_id']}/export", json={"thread_id": out["thread_id"]})
    assert r.status_code == 200, r.text
    f = r.json()
    content = f["content"].encode("utf-8")
    first = f["content"].splitlines()[0]
    assert first.startswith(f"> WATERMARK | exported by {ceo.user_id} {ceo.name} |")
    assert f"| run {out['run_id']} |" in first and f"L{out['hwm']} " in first and f["level"] == out["hwm"]
    assert f["content"].count(first) == 2 and f["content"].count(f"# {data['title']}\n") == 1
    body = out["output"]["text"].split("\n", 1)[1].strip()  # the report minus its title line
    assert body in f["content"]
    check = verify_export(content)
    assert check.ok and check.sha256 == f["sha256"] and f["domain"] == "company"
    rows = _audit(before, "export")
    assert len(rows) == 1 and rows[0]["actor"] == ceo.user_id and rows[0]["decision"] == "allow"
    assert rows[0]["run_id"] == out["run_id"] and rows[0]["level"] == out["hwm"]
    assert rows[0]["details"]["sha256"] == f["sha256"] and rows[0]["details"]["domain"] == "company"
    assert "A-0001" not in str(rows[0]["details"]) and "WATERMARK" not in str(rows[0]["details"])

    # the same file as a download
    g = await app_client.get(f"/api/reports/{out['run_id']}/export", params={"thread_id": out["thread_id"]})
    assert g.status_code == 200 and g.headers["content-type"].startswith("text/markdown")
    assert g.headers["content-disposition"].startswith("attachment; filename=") and verify_export(g.content).ok
    assert g.headers["cache-control"] == "no-store"


async def test_someone_elses_report_cannot_be_exported(app_client, personas, q2):
    await _as(app_client, personas, "ceo")
    out = await run(app_client, REPORT_INPUT, app="report")
    cto = await _as(app_client, personas, "eng_head")
    before = _seq()
    r = await app_client.post(f"/api/reports/{out['run_id']}/export", json={"thread_id": out["thread_id"]})
    _err(r, 404, "not_found")  # someone else's run and a missing one look the same
    # ...and the probe is in the audit log: one 'export' deny row, the same reason as a missing run
    rows = _audit(before, "export")
    assert len(rows) == 1 and rows[0]["actor"] == cto.user_id and rows[0]["decision"] == "deny"
    assert rows[0]["reason"] == "not_found_or_not_own" and rows[0]["run_id"] == out["run_id"]
    assert rows[0]["resource"] == f"report:{out['run_id']}" and rows[0]["details"]["thread_id"] == out["thread_id"]
    # an own thread, but a run id that is not in it
    own = await run(app_client, REPORT_INPUT, app="report")
    before = _seq()
    r = await app_client.post(f"/api/reports/{out['run_id']}/export", json={"thread_id": own["thread_id"]})
    _err(r, 404, "not_found")
    for bad in ("run_x", "RUN_" + "A" * 20, "run_" + "a" * 20, "run_sk-" + "A" * 30):
        r = await app_client.post(f"/api/reports/{bad}/export", json={"thread_id": own["thread_id"]})
        _err(r, 404, "not_found")
    r = await app_client.get(f"/api/reports/{own['run_id']}/export")
    _err(r, 404, "not_found")
    rows = _audit(before, "export")
    assert len(rows) == 6 and {a["decision"] for a in rows} == {"deny"}
    assert {a["reason"] for a in rows} == {"not_found_or_not_own"}
    # malformed ids from the URL are not copied into the audit log
    assert [a["resource"] for a in rows[1:4]] == ["report:invalid", "report:invalid", f"report:{'run_' + 'a' * 20}"]
    assert rows[1]["run_id"] is None and rows[4]["run_id"] is None and rows[4]["resource"] == "report:invalid"


async def test_export_by_someone_who_may_not_run_reports_is_refused_and_audited(app_client, personas, q2):
    await _as(app_client, personas, "ceo")
    out = await run(app_client, REPORT_INPUT, app="report")
    for key in ("sales_staff", "finance_staff"):
        p = await _as(app_client, personas, key)
        before = _seq()
        r = await app_client.post(f"/api/reports/{out['run_id']}/export", json={"thread_id": out["thread_id"]})
        _err(r, 403, "forbidden")
        g = await app_client.get(f"/api/reports/{out['run_id']}/export", params={"thread_id": out["thread_id"]})
        _err(g, 403, "forbidden")
        rows = _audit(before, "export")
        assert len(rows) == 2 and all(a["actor"] == p.user_id and a["decision"] == "deny" for a in rows)
        assert {a["reason"] for a in rows} == {"may_not_run_reports"}
        assert {a["details"]["rule"] for a in rows} == {"agent-start-denied"}
        assert rows[0]["run_id"] == out["run_id"]


async def test_cto_exports_the_eng_report_with_its_own_watermark(app_client, personas, q2):
    cto = await _as(app_client, personas, "eng_head")
    out = await run(app_client, REPORT_INPUT, app="report")
    assert out["output"]["data"]["export"] == {"domain": "eng", "allowed": True}
    assert "法务：整个部分无权限查看或尚未发布" in out["output"]["text"]
    r = await app_client.post(f"/api/reports/{out['run_id']}/export", json={"thread_id": out["thread_id"]})
    assert r.status_code == 200 and f"exported by {cto.user_id} " in r.json()["content"].splitlines()[0]
    assert r.json()["domain"] == "eng"


async def test_people_without_the_report_app_are_refused(app_client, personas, q2):
    for key in ("sales_staff", "finance_staff", "platform_admin", "auditor"):
        await _as(app_client, personas, key)
        r = await app_client.post("/api/runs", json={"app": "report", "input": REPORT_INPUT})
        _err(r, 403, "forbidden")


async def test_report_never_falls_back_to_external_when_local_is_down(app_client, personas, q2, fake_llm):
    await _as(app_client, personas, "ceo")
    set_switch_owner("local_down", True)
    try:
        out = await run(app_client, REPORT_INPUT, app="report")
    finally:
        set_switch_owner("local_down", False)
    assert out["status"] == "error" and out["error"]["code"] == "local_unavailable"
    assert out["output"]["text"] == "" and not [c for c in fake_llm.calls if c["route"] == "external"]


async def test_future_period_is_refused_in_plain_words(app_client, personas, q2):
    await _as(app_client, personas, "ceo")
    out = await run(app_client, "2026-12 管理层报告", app="report")
    assert out["status"] == "done" and out["output"]["data"]["refused"] is True
    assert "还没有结束" in out["output"]["text"]


# ---- metrics: listing and visibility ------------------------------------------------------------

async def test_metric_list_says_who_may_do_what(app_client, personas, q2):
    expect = {"finance_head": (True, True), "finance_staff": (False, True), "ceo": (False, True),
              "sales_staff": (False, False), "eng_head": (False, False)}
    for key, (sign, drafts) in expect.items():
        await _as(app_client, personas, key)
        r = await app_client.get("/api/metrics")
        assert r.status_code == 200, (key, r.text)
        body = r.json()
        assert body["can"] == {"sign": sign, "publish": sign, "refresh": sign, "drafts": drafts}, key
        assert len(body["metrics"]) == 26 and all("sql" not in m for m in body["metrics"])
        # the CFO's refresh offers ended periods, including ones with no snapshot yet; nobody else gets any
        if sign:
            assert body["refreshable"][:2] == ["2026-09", "2026-08"] and "2026-Q3" in body["refreshable"]
            assert "2025-10" in body["refreshable"] and "2026-10" not in body["refreshable"]
            assert set(body["refreshable"]) - set(body["periods"]), "a period without snapshots is offered"
        else:
            assert body["refreshable"] == [], key
    for key in ("platform_admin", "auditor"):
        await _as(app_client, personas, key)
        _err(await app_client.get("/api/metrics"), 403, "forbidden")
        _err(await app_client.get("/api/metrics/board", params={"period": REPORT_PERIOD}), 403, "forbidden")


async def test_board_shows_each_reader_only_their_statuses(app_client, personas, q2):
    await _as(app_client, personas, "sales_staff")
    board = (await app_client.get("/api/metrics/board", params={"period": REPORT_PERIOD})).json()
    by = {m["metric_id"]: m for m in board["metrics"]}
    statuses = {r["status"] for m in board["metrics"] for r in m["rows"]}
    assert statuses == {"published"}  # never a draft or a signed-only row
    assert by["revenue_recognised"]["rows"] == [] and by["revenue_recognised"]["statuses"] == []
    assert by["arr"]["rows"][0]["display_en"].startswith("EUR ")
    await _as(app_client, personas, "ceo")
    board = (await app_client.get("/api/metrics/board", params={"period": REPORT_PERIOD})).json()
    rev = {m["metric_id"]: m for m in board["metrics"]}["revenue_recognised"]["rows"][0]
    assert rev["status"] == "signed" and rev["provenance"]["signed_by"] == personas["finance_head"].user_id
    assert rev["provenance"]["sql_sha256"] and "sql" not in rev["provenance"]
    for r in [r for m in board["metrics"] for r in m["rows"] if r["suppressed"]]:
        assert r["value"] is None and r["display_zh"] == "[小样本，不展示]"


async def test_single_metric_value_and_errors(app_client, personas, q2):
    await _as(app_client, personas, "eng_head")
    r = await app_client.get("/api/metrics/opex_actual", params={"period": REPORT_PERIOD, "dims": "dept=eng"})
    assert r.status_code == 200 and r.json()["status"] == "published" and r.json()["key"] == "opex_actual@dept=eng"
    hidden = await app_client.get("/api/metrics/opex_actual", params={"period": REPORT_PERIOD, "dims": "dept=sales"})
    assert hidden.json()["status"] == "unavailable" and hidden.json()["value"] is None
    _err(await app_client.get("/api/metrics/nope", params={"period": REPORT_PERIOD}), 404, "not_found")
    _err(await app_client.get("/api/metrics/arr", params={"period": "2026-13"}), 400, "bad_request")
    _err(await app_client.get("/api/metrics/arr", params={"period": REPORT_PERIOD, "dims": "dept=eng"}),
         400, "bad_request")
    _err(await app_client.get("/api/metrics/board"), 400, "bad_request")


# ---- sign-off, publication, refresh -------------------------------------------------------------

async def test_only_the_cfo_signs_publishes_and_refreshes(app_client, personas, q2):
    body = {"period": API_PERIOD, "metric_ids": ["arr", "headcount"]}
    for key in ("finance_staff", "ceo", "eng_head", "sales_staff"):
        await _as(app_client, personas, key)
        before = _seq()
        _err(await app_client.post("/api/metrics/refresh", json={"period": API_PERIOD}), 403, "forbidden")
        _err(await app_client.post("/api/metrics/sign", json=body), 403, "forbidden")
        _err(await app_client.post("/api/metrics/publish", json=body), 403, "forbidden")
        signed = _audit(before, "metric_signed")
        published = _audit(before, "metric_published")
        refreshed = [a for a in _audit(before, "policy_decision") if a["action"] == "metrics.refresh"]
        for rows in (signed, published, refreshed):  # every refusal is in the trail, with the person
            assert len(rows) == 1 and rows[0]["decision"] == "deny" and rows[0]["actor"] == personas[key].user_id

    cfo = await _as(app_client, personas, "finance_head")
    before = _seq()
    r = await app_client.post("/api/metrics/refresh", json={"period": API_PERIOD})
    assert r.status_code == 200 and r.json()["period"] == API_PERIOD and r.json()["metrics"] == 26
    assert [a["actor"] for a in _audit(before, "owner_job")] == ["svc:metrics"]

    # drafts: finance staff read them, a sales rep does not
    await _as(app_client, personas, "finance_staff")
    fs = (await app_client.get("/api/metrics/board", params={"period": API_PERIOD})).json()
    assert {r["status"] for m in fs["metrics"] for r in m["rows"]} == {"draft"} and fs["can"]["sign"] is False
    await _as(app_client, personas, "sales_staff")
    ss = (await app_client.get("/api/metrics/board", params={"period": API_PERIOD})).json()
    assert all(m["rows"] == [] for m in ss["metrics"])

    await login(app_client, cfo.user_id)
    early = await app_client.post("/api/metrics/publish", json=body)
    assert early.status_code == 200 and early.json()["rows"] == 0 and set(early.json()["skipped"]) == {"arr", "headcount"}
    s = await app_client.post("/api/metrics/sign", json=body)
    assert s.status_code == 200 and set(s.json()["changed"]) == {"arr", "headcount"}
    p = await app_client.post("/api/metrics/publish", json=body)
    assert p.status_code == 200 and p.json()["rows"] == s.json()["rows"]
    allowed = [a for e in ("metric_signed", "metric_published") for a in _audit(before, e) if a["decision"] == "allow"]
    assert {a["action"] for a in allowed} >= {"metrics.sign", "metrics.publish"}
    assert all(a["actor"] == cfo.user_id for a in allowed)

    await _as(app_client, personas, "sales_staff")
    ss = {m["metric_id"]: m for m in (await app_client.get("/api/metrics/board",
                                                            params={"period": API_PERIOD})).json()["metrics"]}
    assert ss["arr"]["rows"] and ss["arr"]["rows"][0]["status"] == "published"
    assert ss["customers_active"]["rows"] == []  # never signed: still a draft, still finance's


async def test_sign_errors_follow_section_18(app_client, personas, q2):
    await _as(app_client, personas, "finance_head")
    _err(await app_client.post("/api/metrics/sign", json={"period": API_PERIOD, "metric_ids": ["nope"]}),
         400, "bad_request")
    _err(await app_client.post("/api/metrics/sign", json={"period": API_PERIOD, "metric_ids": []}), 400, "bad_request")
    _err(await app_client.post("/api/metrics/sign", json={"period": "2026-7x", "metric_ids": ["arr"]}),
         400, "bad_request")
    _err(await app_client.post("/api/metrics/refresh", json={"period": "2026-12"}), 400, "bad_request")


async def test_refresh_is_refused_outside_the_demo(app_client, personas, q2, monkeypatch):
    import corpai.api.routes_metrics as rm

    real = rm.settings()

    class _NoDemo:
        demo_login = False

        def __getattr__(self, name):
            return getattr(real, name)

    await _as(app_client, personas, "finance_head")
    monkeypatch.setattr(rm, "settings", lambda: _NoDemo())
    before = _seq()
    _err(await app_client.post("/api/metrics/refresh", json={"period": API_PERIOD}), 403, "forbidden")
    rows = [a for a in _audit(before, "policy_decision") if a["action"] == "metrics.refresh"]
    assert len(rows) == 1 and rows[0]["decision"] == "deny" and _audit(before, "owner_job") == []
    body = (await app_client.get("/api/metrics")).json()
    assert body["can"]["refresh"] is False and body["can"]["sign"] is True
