"""The M4 pages and their demo mode (architecture §14, decision D9; design-review 二.6, 二.10).

Static (pure Python): the pages deny explicitly, render data as text only, show source URLs through
the confirm-first external link, the screening page has no score and its decision needs a reason;
the fixtures carry the M4 scenarios in the shapes the live API returns.
Demo backend (Node, skipped without it): demo mode refuses what the live API refuses.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "ui" / "js"
FIX = ROOT / "ui" / "fixtures"
NODE = shutil.which("node")
SCORE_KEYS = {"score", "total_score", "rank", "ranking", "recommendation", "rating"}


def _code(name: str) -> str:
    src = (JS / name).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _fixture(name: str) -> dict:
    src = (FIX / f"{name}.js").read_text(encoding="utf-8")
    m = re.fullmatch(rf"window\.FIXTURES\.{name} = (.*);\n", src, flags=re.S)
    assert m
    return json.loads(m.group(1))


def _keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


# ---------------------------------------------------------------- static

@pytest.mark.parametrize("name", ["view_sales.js", "view_hr.js", "demo_sales.js", "demo_hr.js"])
def test_pages_render_data_as_text_only(name):
    code = _code(name)
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in code, (name, bad)
    assert "fetch(" not in code, "pages talk to the API through KB.api only"


def test_every_m4_page_denies_explicitly():
    sales, hr = _code("view_sales.js"), _code("view_hr.js")
    assert "KB.appPage.denied(root, APP)" in sales
    assert "KB.appPage.denied(root, app)" in hr
    for route in ("hr", "headcount", "candidates"):
        assert re.search(rf"KB\.views\.{route} = \{{\s*render: function \(root\) \{{\s*if \(!header\(", hr), route


def test_sales_page_shows_sources_in_full_with_confirm_and_never_page_text():
    code = _code("view_sales.js")
    assert code.count("U.extLink(u)") >= 2  # sources and flagged pages: full URL, confirm before opening
    assert ".text" not in code.replace("output.text", "").replace(".textContent", "")  # no page text field
    assert '"/followup "' in code and "thread_id: run.thread_id" in code


def test_screening_page_has_no_score_and_needs_a_reason():
    code = _code("view_hr.js")
    # the only "score" on the page is the shared notice that there is none
    assert "score" not in code.lower().replace('t("data_no_score")', "")
    assert "hr_reason_required" in code and "if (!reason)" in code
    assert "buttons.forEach(function (b) { b.disabled = !ok; })" in code  # disabled until a reason is typed
    assert "/api/hr/screenings/decision" in code and "data_no_score" in code
    i18n = (JS / "i18n_hr.js").read_text(encoding="utf-8")
    assert "GDPR" in i18n and "Annex III" in i18n and "第 22 条" in i18n


def test_sales_fixture_scenarios():
    fx = _fixture("sales")
    by = {(r["persona_key"], r["input"]): r["run"] for r in fx["runs"]}
    staff = by[("sales_staff", "Havenmeer Distributie B.V.")]["output"]["data"]
    steps = {s["step"]: s for s in staff["steps"]}
    assert steps["web_extract"]["route"] == "external" and steps["web_extract"]["level"] == 1
    assert steps["intent"]["route"] == "code" and steps["strategy"]["route"] == "local"
    flagged = by[("ceo", "Elbstrom Paketdienst GmbH")]["output"]["data"]["web"]["flagged_urls"]
    assert flagged
    denied = by[("sales_staff", "Elbstrom Paketdienst GmbH")]["output"]["data"]
    assert denied["intent"]["status"] == "no_permission" and denied["playbook"] is None
    follow = by[("sales_staff", "/followup CT-00004")]
    assert follow["status"] == "waiting_approval" and follow["pending_approval"]["tier"] == 3
    pid = follow["pending_approval"]["proposal_id"]
    boxes = fx["proposals"]["by_persona"]
    (mine,) = [p for p in boxes["sales_staff"]["mine"] if p["proposal_id"] == pid]
    (head,) = [p for p in boxes["sales_head"]["to_approve"] if p["proposal_id"] == pid]
    assert mine["can_approve"] is False and head["can_approve"] is True and mine["params"] == head["params"]
    # sales_head has no recorded profile run: the shared sample check (tests/ui) stays meaningful
    assert not [r for r in fx["runs"] if r["persona_key"] == "sales_head"]


def test_hr_fixture_scenarios():
    fx = _fixture("hr")
    screening = [r for r in fx["runs"] if r["app"] == "hr_screening"]
    assert screening and not (_keys(screening) & SCORE_KEYS)
    assert all(r["run"]["route"] == "local" and r["run"]["hwm"] == 4 for r in screening)
    assert any(r["run"]["output"]["data"]["embedded_instruction"] for r in screening)
    hc = {r["persona_key"]: r["run"] for r in fx["runs"] if r["app"] == "hr_headcount"}
    assert hc["hr_staff"]["route"] == "external" and hc["hr_staff"]["output"]["data"]["budget"]["allowed"] is False
    assert hc["hr_head"]["route"] == "local" and hc["hr_head"]["output"]["data"]["budget"]["allowed"] is True
    cands = fx["candidates"]["hr_candidates"]
    assert [r["req_id"] for r in cands["sales_head"]["requisitions"]] == ["R-03"]
    assert cands["eng_staff"]["requisitions"] == [] and cands["eng_staff"]["denied"]
    access = fx["resumes"]["access"]["hr_candidates"]["sales_head"]
    assert access and all(fx["resumes"]["items"][c]["candidate"]["req_id"] == "R-03" for c in access)


# ---------------------------------------------------------------- demo backend (Node)

@pytest.fixture(scope="module")
def r() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    proc = subprocess.run([NODE, str(Path(__file__).with_name("demo_m4.js"))], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _err(r, name, status, code=None):
    res = r[name]
    assert res["ok"] is False and res["status"] == status, (name, res)
    if code:
        assert res["code"] == code, (name, res)


def test_demo_followup_only_after_a_profile_in_the_same_thread(r):
    refused = r["followup_without_profile"]["value"]
    assert refused["output"]["data"]["refused"] == "no_profile" and refused["pending_approval"] is None
    ok = r["followup_in_thread"]["value"]
    assert ok["status"] == "waiting_approval" and ok["thread_id"] == r["staff_profile"]["value"]["thread_id"]
    _err(r, "followup_other_contact", 404, "demo_not_recorded")


def test_demo_sales_denials_and_switches(r):
    assert r["staff_elbstrom"]["value"]["output"]["data"]["intent"]["status"] == "no_permission"
    assert {a["account_id"] for a in r["staff_accounts"]["value"]["accounts"]} == {"A-0002", "A-0004", "A-0006"}
    _err(r, "eng_accounts", 403, "forbidden")
    _err(r, "eng_sales_run", 403, "forbidden")
    down = r["staff_profile_local_down"]["value"]
    assert down["status"] == "error" and down["error"]["code"] == "local_unavailable" and not down["output"]["text"]


def test_demo_decision_mirrors_the_live_rules(r):
    assert r["screen"]["value"]["output"]["data"]["awaiting_decision"] is True
    _err(r, "decide_no_reason", 400, "bad_request")
    _err(r, "decide_bad_decision", 400, "bad_request")
    _err(r, "decide_wrong_candidate", 400, "bad_request")
    _err(r, "decide_other_persona", 404, "not_found")
    _err(r, "decide_not_hr", 403, "forbidden")
    ok = r["decide_ok"]["value"]
    assert ok["screening_id"].startswith("scr_") and ok["run"]["output"]["data"]["decision"]["decision"] == "hold"
    assert "check in the interview" not in json.dumps(ok)
    _err(r, "decide_twice", 404, "not_found")
    assert r["screen_local_down"]["value"]["status"] == "error"
    _err(r, "decide_after_failed_run", 404, "not_found")
    events = [e for e in r["audit"]["value"]["entries"] if e["event"] == "hr_decision"]
    assert {e["decision"] for e in events} == {"allow", "deny"}
    assert all("check in the interview" not in json.dumps(e) for e in events)


def test_demo_candidates_follow_the_hiring_relation(r):
    reqs = r["sh_candidates"]["value"]["requisitions"]
    assert [x["req_id"] for x in reqs] == ["R-03"]
    assert r["sh_resume_own"]["value"]["trust"] == "untrusted"
    _err(r, "sh_resume_other", 404, "not_found")
    other = r["sh_req_other"]["value"]
    assert other["requisitions"][0]["denied"] is True and other["denied"]
    _err(r, "sh_screening_app", 403, "forbidden")
    assert r["eng_candidates"]["value"]["requisitions"] == [] and r["eng_candidates"]["value"]["denied"]
    _err(r, "auditor_candidates", 403, "forbidden")


def test_demo_headcount_by_persona(r):
    staff, head = r["hc_staff"]["value"], r["hc_head"]["value"]
    assert staff["route"] == "external" and staff["hwm"] == 2
    assert head["route"] == "local" and head["hwm"] == 3
