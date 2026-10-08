"""The M3 pages and their demo data (ui/js/view_report.js, view_metrics.js, demo_report.js,
fixtures/report.js).

Static checks (no browser): the pages deny explicitly, put numbers on the page only as the metric
layer rendered them, never build HTML from data, and use the same placeholder grammar as the metric
layer. The recorded fixtures are checked against the real code: each recorded report re-renders from
its own table to exactly its text, every number in it verifies, the CEO's has the planted issues, the
CTO's differs and says what is denied, small groups carry no figure, and the board rows carry the ACLs
the metric definitions give. The demo backend runs under Node and must refuse what the live API refuses.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from corpai.agents.report.compose import table_from_view
from corpai.metrics import MetricValue, format_value, load_metrics, render, verify_numbers, verify_template
from corpai.metrics.numbers import PLACEHOLDER_RE
from corpai.types import Level

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "ui" / "js"
HARNESS = Path(__file__).with_name("report_demo_harness.js")
NODE = shutil.which("node")
RECORDED = ("ceo", "eng_head", "finance_head", "sales_head", "legal_head", "hr_head")


def _code(name: str) -> str:
    src = (JS / name).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


@pytest.fixture(scope="module")
def fx() -> dict:
    src = (ROOT / "ui" / "fixtures" / "report.js").read_text(encoding="utf-8")
    m = re.fullmatch(r"window\.FIXTURES\.report = (.*);\n?", src, flags=re.S)
    assert m
    return json.loads(m.group(1))


def _runs(fx: dict, lang: str = "zh") -> dict[str, dict]:
    """persona -> the recorded report in `lang` (each persona is recorded in Chinese and in English)."""
    return {i["persona_key"]: i["run"] for i in fx["runs"] if i["run"]["output"]["data"]["lang"] == lang}


# ---- static checks of the pages -------------------------------------------------------------------

def test_pages_deny_explicitly_and_talk_to_the_m3_api():
    report, metrics = _code("view_report.js"), _code("view_metrics.js")
    assert "KB.appPage.denied(root, \"report\")" in report and "if (!KB.appPage.hasApp(\"report\"))" in report
    assert 'KB.api.post("/api/runs", {app: "report"' in report
    assert 'KB.api.post("/api/reports/" + encodeURIComponent(run.run_id) + "/export", {thread_id: run.thread_id})' in report
    assert "C.denied(t(\"metrics_denied_platform\"))" in metrics
    for path in ("/api/metrics", "/api/metrics/board?period=", "/api/metrics/sign", "/api/metrics/publish",
                 "/api/metrics/refresh"):
        assert f'"{path}' in metrics, path
    # sign-off controls exist only for the head of finance (the server checks again)
    assert "can.sign ? el(\"input\"" in metrics and "if (can.sign) slot.appendChild(actions(" in metrics


def test_numbers_reach_the_page_only_as_rendered_by_the_metric_layer():
    report = _code("view_report.js")
    for bad in ("toLocaleString", "toFixed", "Intl.NumberFormat", "parseFloat", "Number("):
        assert bad not in report, bad  # the page never formats or computes a figure itself
    assert "entry.display_en : entry.display_zh" in report
    # notices are styled as notices, not errors and never zeros
    assert 'entry.status === "unavailable") cls = "chip status status-bad"' in report
    assert 'entry.suppressed) cls = "chip status status-muted"' in report


def test_placeholder_grammar_matches_the_metric_layer():
    js = re.search(r"var PLACEHOLDER = /(.*)/g;", _code("view_report.js")).group(1)
    assert js.replace("\\/", "/") == PLACEHOLDER_RE.pattern


def test_export_downloads_in_live_and_shows_the_watermark_in_demo():
    report = _code("view_report.js")
    live = report[report.index('if (KB.api.mode === "live")'):report.index("U.toast(t(\"report_export_done\"")]
    assert "saveFile(f.filename, f.content)" in live and "report_export_demo" in live
    assert "new Blob([text]" in report and "URL.revokeObjectURL" in report


# ---- the recorded reports -------------------------------------------------------------------------

def test_recordings_exist_for_the_management_personas(fx):
    from corpai.agents.report.request import parse_request
    from corpai.clock import today

    assert len(fx["runs"]) == 2 * len(RECORDED)
    for lang in ("zh", "en"):
        runs = _runs(fx, lang)
        assert set(runs) == set(RECORDED), lang
        for key, run in runs.items():
            assert run["app"] == "report" and run["status"] == "done" and run["error"] is None, key
            assert run["hwm"] >= 3 and run["route"] == "local", key
            assert run["output"]["data"]["period"] == "2026-Q3", key
            # the demo shows the model's narrative: it passed every number, id, date and issue check
            assert run["output"]["data"]["used_fallback"] is False, (key, lang, run["output"]["data"]["problems"])
    for item in fx["runs"]:  # each recording is written in its input's language, as live reads it
        assert parse_request(item["input"], today()).lang == item["run"]["output"]["data"]["lang"]


def test_recorded_reports_rerender_and_verify(fx):
    for item in fx["runs"]:
        key, run = item["persona_key"], item["run"]
        data = run["output"]["data"]
        full = table_from_view(data["table"])
        assert render(data["template"], full, lang=data["lang"]) == run["output"]["text"], key
        assert verify_template(data["template"], full) == [] and verify_numbers(run["output"]["text"], full) == [], key
        for k, mv in full.items():
            assert data["table"][k]["display_zh"] == format_value(mv, "zh"), (key, k)
            if data["table"][k]["suppressed"]:
                assert data["table"][k]["value"] is None, (key, k)


def test_recorded_ceo_and_cto_reports_differ_and_deny_explicitly(fx):
    denied = {"zh": "法务：整个部分无权限查看或尚未发布", "en": "no permission for this whole section"}
    for lang in ("zh", "en"):
        runs = _runs(fx, lang)
        ceo, cto = runs["ceo"], runs["eng_head"]
        for ref in ("A-0001", "C-0003", "P-02", "R-02", "C-0005"):
            assert ref in ceo["output"]["text"], (lang, ref)
        sections = {s["domain"]: s["status"] for s in cto["output"]["data"]["sections"]}
        assert sections["legal"] == "no_permission" and denied[lang] in cto["output"]["text"], lang
        assert any(e["t"] == "tool" and e["decision"] == "deny" for e in cto["events"])
        ceo_keys = {k for k, v in ceo["output"]["data"]["table"].items() if v["status"] != "unavailable"}
        cto_keys = {k for k, v in cto["output"]["data"]["table"].items() if v["status"] != "unavailable"}
        assert ceo_keys != cto_keys and "A-0001" not in cto["output"]["text"], lang
        assert ceo["output"]["data"]["export"]["domain"] == "company"
        assert cto["output"]["data"]["export"]["domain"] == "eng"


def test_board_rows_carry_the_acls_of_their_definitions(fx, personas):
    defs = load_metrics()
    board = fx["metrics"]
    assert {d["metric_id"] for d in board["defs"]} == set(defs)
    assert set(board["periods"]) == {"2026-Q3", "2026-09"}
    assert {r["status"] for r in board["periods"]["2026-Q3"]["rows"]} == {"published"}
    assert {r["status"] for r in board["periods"]["2026-09"]["rows"]} == {"draft"}
    for period, b in board["periods"].items():
        for r in b["rows"]:
            md = defs[r["metric_id"]]
            dept = r["dims"].get("dept") if "dept" in md.dims else md.dept
            assert r["acl_published"] == list(md.published_acl(dept)), (period, r["key"])
            assert set(r["acl_draft"]) <= {"dept:finance", "head:finance", "role:ceo"}
            mv = MetricValue(metric_id=r["metric_id"], period=period, dims=r["dims"],
                             value=None if r["value"] is None else __import__("decimal").Decimal(r["value"]),
                             unit=r["unit"], status=r["status"], level=Level(r["level"]), suppressed=r["suppressed"])
            assert r["display_zh"] == format_value(mv, "zh"), (period, r["key"])
            if r["suppressed"]:
                assert r["value"] is None
    # the groups the demo filters with are the directory's own
    for key, groups in board["groups"].items():
        assert groups == sorted(personas[key].groups), key


# ---- the demo backend under Node --------------------------------------------------------------------

@pytest.fixture(scope="module")
def r() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    proc = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _err(r, name, status, code=None):
    res = r[name]
    assert res["ok"] is False, f"{name} should have been refused: {res}"
    assert res["status"] == status, (name, res)
    if code:
        assert res["code"] == code, (name, res)


def test_demo_report_runs_follow_the_persona_and_the_shared_rules(r, fx):
    runs, en = _runs(fx), _runs(fx, "en")
    # the recording in the input's language, as live writes it
    assert r["ceo_run"]["value"]["run_id"] == runs["ceo"]["run_id"]
    assert r["ceo_run_en"]["value"]["run_id"] == en["ceo"]["run_id"]
    assert r["ceo_run_q_first"]["value"]["run_id"] == en["ceo"]["run_id"]
    assert r["ceo_run_zh_quarter"]["value"]["run_id"] == runs["ceo"]["run_id"]
    assert r["cto_run"]["value"]["run_id"] == runs["eng_head"]["run_id"]
    _err(r, "ceo_run_unrecorded_period", 404, "demo_not_recorded")
    _err(r, "sales_staff_run", 403, "forbidden")
    raised = r["ceo_run_raised"]["value"]
    assert raised["hwm"] == 4 and raised["route"] == "local"
    down = r["ceo_run_local_down"]["value"]
    assert down["status"] == "error" and down["error"]["code"] == "local_unavailable" and not down["output"]["text"]


def test_demo_export_is_own_runs_only_and_watermarked(r, fx):
    f = r["ceo_export"]["value"]
    first = f["content"].splitlines()[0]
    run_id = _runs(fx)["ceo"]["run_id"]
    assert first.startswith("> WATERMARK | exported by u0001 ") and f"| run {run_id} |" in first
    assert f["content"].count(first) == 2 and f["domain"] == "company" and f["filename"].endswith(".md")
    assert f["content"].count("# 管理层报告 2026-Q3\n") == 1  # the title once, as in a live export
    _err(r, "cto_exports_ceo_run", 404, "not_found")
    _err(r, "ceo_export_no_thread", 404, "not_found")
    assert r["cto_export"]["value"]["domain"] == "eng"
    exports = [e for e in r["audit"] if e["event"] == "export"]
    assert [(e["actor"], e["decision"]) for e in exports] == [("u0001", "allow"), ("u0020", "allow")]


def test_demo_metrics_filter_like_row_level_security(r):
    _err(r, "metrics_before_login", 401, "unauthenticated")
    _err(r, "admin_metrics", 403, "forbidden")
    _err(r, "auditor_board", 403, "forbidden")
    sales_q3 = r["sales_board_q3"]["value"]["metrics"]
    assert {row["status"] for m in sales_q3 for row in m["rows"]} == {"published"}
    by = {m["metric_id"]: m for m in sales_q3}
    assert by["arr"]["rows"] and not by["cash_balance"]["rows"]  # management-only stays hidden
    assert all(not m["rows"] for m in r["sales_board_09_before"]["value"]["metrics"])  # drafts are finance's
    fs = r["fs_board_09"]["value"]
    assert {row["status"] for m in fs["metrics"] for row in m["rows"]} == {"draft"} and fs["can"]["sign"] is False
    assert r["fs_list"]["value"]["can"] == {"sign": False, "publish": False, "refresh": False, "drafts": True}
    assert r["sales_list"]["value"]["can"]["drafts"] is False and r["sales_list"]["value"]["periods"] == ["2026-Q3"]
    assert r["ceo_list"]["value"]["can"] == {"sign": False, "publish": False, "refresh": False, "drafts": True}
    assert r["cfo_list"]["value"]["can"] == {"sign": True, "publish": True, "refresh": True, "drafts": True}


def test_demo_sign_off_is_the_cfos_and_audited(r):
    for name in ("sales_sign", "fs_sign", "ceo_publish", "fs_refresh"):
        _err(r, name, 403, "forbidden")
    _err(r, "cfo_unknown", 400, "bad_request")
    _err(r, "cfo_one_unknown", 404, "not_found")
    early = r["cfo_publish_early"]["value"]
    assert early["rows"] == 0 and early["skipped"] == ["arr"]
    assert r["cfo_sign"]["value"]["changed"] == ["arr", "headcount"]
    assert r["cfo_publish"]["value"]["changed"] == ["arr"]
    assert r["cfo_refresh"]["ok"] and r["cfo_one"]["value"]["status"] == "published"
    assert r["cfo_one"]["value"]["provenance"]["signed_by"] == "u0040"
    after = {m["metric_id"]: m for m in r["sales_board_09_after"]["value"]["metrics"]}
    assert after["arr"]["rows"] and after["arr"]["rows"][0]["status"] == "published"
    assert not after["headcount"]["rows"]  # signed, not published: still finance's and the CEO's
    fs_after = {m["metric_id"]: m for m in r["fs_board_09_after"]["value"]["metrics"]}
    assert {row["status"] for row in fs_after["headcount"]["rows"]} == {"signed"}
    trail = [(e["event"], e["actor"], e["decision"]) for e in r["audit"]
             if e["event"] in ("metric_signed", "metric_published")]
    assert ("metric_signed", "u0011", "deny") in trail and ("metric_signed", "u0041", "deny") in trail
    assert ("metric_published", "u0001", "deny") in trail
    assert ("metric_signed", "u0040", "allow") in trail and ("metric_published", "u0040", "allow") in trail
    refresh = [(e["actor"], e["decision"]) for e in r["audit"] if e["action"] == "metrics.refresh"]
    assert refresh == [("u0041", "deny"), ("u0040", "allow")]
