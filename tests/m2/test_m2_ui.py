"""M2 legal page, strings, demo hooks and recordings (architecture §14, D9, phase-B §18).

Static checks of ui/js/view_legal.js, i18n_legal.js, demo_legal.js and fixtures/legal.js, plus a
Node run of the demo backend with the legal extension (skipped without Node):
- the page denies explicitly, renders data as text only, mirrors the server's draft templates and
  upload limits, and every string it names exists in zh and en;
- the recordings cover the M2 demo: C-0004 with the injection flagged (effect none), C-0005 missing
  its DPA terms, a draft, the contracts due in 2026-11 (C-0003, notice deadline 2026-10-16) and a
  send waiting for the head of legal; the head of legal's and the CEO's own review and due list; quotes
  match the reviewed text; L3+ is local;
- demo mode refuses what live refuses: other departments get 403 on runs and on /api/legal/*,
  the lawyer cannot approve her own send (four-eyes), local_down stops reviews but not the SQL-only
  due list, a review id outside your list is 404.
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
VIEW = JS / "view_legal.js"
DEMO = JS / "demo_legal.js"
I18N = JS / "i18n_legal.js"
FIX = ROOT / "ui" / "fixtures" / "legal.js"
HARNESS = Path(__file__).with_name("legal_demo_harness.js")
NODE = shutil.which("node")
RUN_KEYS = {"run_id", "thread_id", "app", "status", "output", "hwm", "route", "events", "pending_approval", "error"}


def _code(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


@pytest.fixture(scope="module")
def strings() -> dict[str, dict[str, str]]:
    src = _code(I18N).strip()
    m = re.fullmatch(r'KB\.lang\.extend\("legal", (\{.*\})\);', src, flags=re.S)
    assert m
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def fx() -> dict:
    m = re.fullmatch(r"window\.FIXTURES\.legal = (.*);\n?", FIX.read_text(encoding="utf-8"), flags=re.S)
    assert m
    return json.loads(m.group(1))


# ---------------------------------------------------------------- page and strings

def test_page_denies_explicitly_and_renders_text_only():
    code = _code(VIEW)
    assert "KB.appPage.denied(root, APP)" in code and "if (!KB.appPage.hasApp(APP))" in code
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in code
    assert "fetch(" not in code, "requests go through KB.api only"
    assert 'KB.api.post("/api/runs"' in code and "JSON.stringify(request)" in code


def test_page_mirrors_the_servers_templates_and_limits():
    from corpai.agents.legal import uploads
    from corpai.api.routes_runs import MAX_INPUT_CHARS
    from corpai.legal import TEMPLATES

    code = _code(VIEW)
    block = code[code.index("var TEMPLATES = {"):code.index("};", code.index("var TEMPLATES = {")) + 1]
    page = json.loads(block[block.index("{"):])
    assert page == {k: list(t.variables) for k, t in TEMPLATES.items()}
    assert int(re.search(r"var MAX_UPLOAD = (\d+);", code).group(1)) == uploads.MAX_CHARS
    inline = int(re.search(r"var INLINE_MAX = (\d+);", code).group(1))
    assert inline + 200 < MAX_INPUT_CHARS, "pasted text sent inline must fit the run input with its JSON wrapper"


def test_every_legal_string_the_page_names_exists_in_both_languages(strings):
    zh, en = strings["zh"], strings["en"]
    assert set(zh) == set(en)
    assert all(k.startswith(("legal_", "app_legal_review")) for k in zh), "keys stay in the legal namespace"
    for path in (VIEW,):
        # whole keys only; "legal_risk_" + k and the like are dynamic families, checked below
        literal = set(re.findall(r'"(legal_[a-z0-9_]*[a-z0-9])"', _code(path))) - {"legal_review"}
        assert literal <= set(zh), literal - set(zh)
    from corpai.legal.draft import VARIABLES

    families = {
        "legal_risk_{}": ["high", "medium", "low"],
        "legal_verdict_{}": ["acceptable", "fallback", "unacceptable", "missing"],
        "legal_pos_{}": ["preferred", "fallback", "fixed"],
        "legal_source_{}": ["stored", "pasted", "upload"],
        "legal_var_{}": list(VARIABLES),
    }
    missing = [f.format(v) for f, vals in families.items() for v in vals if f.format(v) not in zh]
    assert missing == []
    for k, v in zh.items():
        assert set(re.findall(r"\{(\w+)\}", v)) == set(re.findall(r"\{(\w+)\}", en[k])), k
    assert "已隔离" in zh["legal_findings_title"] and "无任何效果" in zh["legal_findings_title"]


def test_finding_notice_text_matches_the_page(strings):
    """The run's notice event (corpai/agents/legal/graph.py) and the page say the same thing."""
    src = (ROOT / "corpai" / "agents" / "legal" / "graph.py").read_text(encoding="utf-8")
    assert "已隔离，无任何效果" in src and '"effect": "none"' in src


# ---------------------------------------------------------------- recordings

def _runs(fx: dict) -> list[dict]:
    return fx["runs"] + fx["no_model_runs"]


def _by(fx: dict, mode: str) -> list[dict]:
    return [i for i in _runs(fx) if i["run"]["output"]["data"].get("mode") == mode]


def test_recordings_have_the_run_shape_and_routing_rules(fx):
    for item in _runs(fx):
        run = item["run"]
        assert set(run) == RUN_KEYS and item["app"] == run["app"] == "legal_review"
        assert item["persona_key"] in ("legal_staff", "legal_head", "ceo")
        if run["hwm"] >= 3 and run["route"] is not None:
            assert run["route"] == "local"
        for e in run["events"]:
            assert e["t"] in ("classify", "retrieval", "tool", "hwm", "route", "approval", "notice")
            if e["t"] == "route":
                assert e["route"] == "local"
    for item in fx["no_model_runs"]:
        run = item["run"]
        assert run["route"] is None and not [e for e in run["events"] if e["t"] == "route"], \
            "a run without a model call names no route"
    for item in fx["runs"]:
        assert item["run"]["route"] == "local"


def test_recorded_reviews_flag_the_injection_and_the_missing_dpa(fx):
    reviews = {i["run"]["output"]["data"]["review"]["contract_id"]: i["run"] for i in _by(fx, "review")}
    c4, c5 = reviews["C-0004"], reviews["C-0005"]
    r4 = c4["output"]["data"]["review"]
    # the planted instruction in clause 9 is found by code (and the model); every finding had no effect.
    # The live model may flag more than code does (an earlier recording flagged a plain sentence of clause 6,
    # model only, before the prompt limited the flag to the clause under review): over-flagging only asks a
    # lawyer to read a passage, so it is allowed here.
    assert r4["findings"] and all(f["kind"] == "embedded_instruction" and f["effect"] == "none" for f in r4["findings"])
    assert any(f["clause_id"] == "9" and "code" in f["detected_by"] for f in r4["findings"])
    assert any(e["t"] == "notice" and e.get("code") == "embedded_instruction" for e in c4["events"])
    assert {c["clause_id"]: c["verdict"] for c in r4["clauses"]}["9"] == "unacceptable"
    assert any(c["verdict"] == "missing" for c in c5["output"]["data"]["review"]["clauses"])
    for run in reviews.values():
        rv, doc = run["output"]["data"]["review"], run["output"]["data"]["document"]
        assert rv["route"] == "local" and run["pending_approval"] is None
        for c in rv["clauses"]:
            assert re.fullmatch(r"KB-\d{2}@\d{4}-\d{2}-\d{2}", c["rule"])
            for q in c["quotes"]:
                assert doc["text"][q["start"]:q["end"]] == q["text"]
        assert [e for e in run["events"] if e["t"] == "tool"] == \
            [e for e in run["events"] if e["t"] == "tool" and e["tool"] == "get_contract"]


def test_recorded_due_draft_and_send(fx):
    due = _by(fx, "due")[0]["run"]["output"]["data"]
    assert due["month"] == "2026-11" and [r["contract_id"] for r in due["contracts"]] == ["C-0003"]
    assert due["contracts"][0]["notice_deadline"] == "2026-10-16"
    draft = _by(fx, "draft")[0]["run"]
    assert draft["status"] == "waiting_approval" and draft["pending_approval"]["tier"] == 2
    send = _by(fx, "send")[0]["run"]
    assert send["status"] == "waiting_approval" and send["pending_approval"]["tier"] == 3
    assert send["pending_approval"]["tool"] == "legal.send_contract"
    pid = send["pending_approval"]["proposal_id"]
    boxes = fx["proposals"]["by_persona"]
    mine = next(p for p in boxes["legal_staff"]["mine"] if p["proposal_id"] == pid)
    head = next(p for p in boxes["legal_head"]["to_approve"] if p["proposal_id"] == pid)
    assert mine["can_approve"] is False and head["can_approve"] is True
    assert mine["params"] == head["params"] and mine["params_hash"] == head["params_hash"]
    assert mine["params"]["contract_id"] == "C-0004" and mine["params"]["to"].endswith(".example")
    assert boxes["legal_staff"]["to_approve"] == []


def test_recorded_lists_follow_row_level_security(fx):
    staff = {c["contract_id"] for c in fx["contracts"]["legal_staff"]}
    head = {c["contract_id"] for c in fx["contracts"]["legal_head"]}
    assert "C-0006" not in staff and "C-0006" in head and staff < head
    assert all("text" not in c for lst in fx["contracts"].values() for c in lst)
    # reviews are readable by their requester, the head of legal and the handling lawyer only: the CEO's
    # list holds the CEO's own review, the head of legal's every review
    staff_reviews = {r["review_id"] for r in fx["reviews"]["legal_staff"]}
    assert fx["reviews"]["ceo"] and all(r["requested_by"] == "u0001" for r in fx["reviews"]["ceo"])
    assert staff_reviews and not staff_reviews & {r["review_id"] for r in fx["reviews"]["ceo"]}
    assert staff_reviews < {r["review_id"] for r in fx["reviews"]["legal_head"]}


def test_head_of_legal_and_ceo_have_their_own_recordings(fx):
    """issues-log WR-07: everyone who may start the app has a recorded review and due list; the head of
    legal's review is of a contract legal staff cannot see."""
    for key in ("legal_staff", "legal_head", "ceo"):
        modes = {i["run"]["output"]["data"].get("mode") for i in _runs(fx) if i["persona_key"] == key}
        assert {"review", "due"} <= modes, key
    staff = {c["contract_id"] for c in fx["contracts"]["legal_staff"]}
    head = [i["run"] for i in _by(fx, "review") if i["persona_key"] == "legal_head"]
    assert head and all(r["output"]["data"]["review"]["contract_id"] not in staff for r in head)
    for item in _by(fx, "due"):
        assert [c["contract_id"] for c in item["run"]["output"]["data"]["contracts"]] == ["C-0003"], item["persona_key"]


# ---------------------------------------------------------------- demo backend under Node

@pytest.fixture(scope="module")
def r() -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    proc = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _ok(r, name):
    assert r[name]["ok"], (name, r[name])
    return r[name]["value"]


def _err(r, name, status, code):
    assert r[name] == {"ok": False, "status": status, "code": code}, (name, r[name])


def test_demo_finds_recordings_by_meaning(r):
    c4 = _ok(r, "review_c4")
    assert c4["output"]["data"]["review"]["findings"][0]["effect"] == "none" and c4["route"] == "local"
    for name in ("review_c4_plain", "review_c4_keys_reordered"):
        assert _ok(r, name)["output"]["data"]["review"]["review_id"] == c4["output"]["data"]["review"]["review_id"]
    assert any(c["verdict"] == "missing" for c in _ok(r, "review_c5")["output"]["data"]["review"]["clauses"])
    for name in ("due_default", "due_plain"):
        due = _ok(r, name)["output"]["data"]
        assert [x["contract_id"] for x in due["contracts"]] == ["C-0003"], name
    _err(r, "review_unrecorded", 404, "demo_not_recorded")
    _err(r, "send_other_recipient", 404, "demo_not_recorded")
    assert _ok(r, "send")["status"] == "waiting_approval"
    assert _ok(r, "draft")["pending_approval"]["tier"] == 2


def test_demo_legal_routes(r):
    assert {c["contract_id"] for c in _ok(r, "contracts")["contracts"]} >= {"C-0003", "C-0004", "C-0005"}
    assert r["rid"] in [x["review_id"] for x in _ok(r, "reviews")["reviews"]]
    assert _ok(r, "review_detail")["review_id"] == r["rid"]
    _err(r, "review_missing", 404, "not_found")
    assert _ok(r, "upload")["upload_id"].startswith("upl_")
    _err(r, "review_upload", 404, "demo_not_recorded")
    modes = sorted(x["mode"] for x in _ok(r, "samples")["requests"])
    assert modes == ["draft", "due", "review", "review", "send"]


def test_demo_refuses_what_live_refuses(r):
    _err(r, "sales_run", 403, "forbidden")
    for name in ("sales_contracts", "sales_reviews", "sales_review_detail", "sales_upload", "sales_samples"):
        _err(r, name, 403, "forbidden")
    _err(r, "self_approve", 403, "forbidden")      # four-eyes on the lawyer's own send
    _err(r, "ceo_review_detail", 404, "not_found")  # not among the reviews the CEO may read
    assert _ok(r, "ceo_contracts")["count"] > 0


def test_demo_head_of_legal_approves_the_send(r):
    assert r["pid"] in [p["proposal_id"] for p in _ok(r, "head_inbox")]
    assert _ok(r, "head_review_detail")["review_id"] == r["rid"]
    assert _ok(r, "head_approve")["proposal"]["status"] == "executed"


def test_demo_local_down_stops_reviews_but_not_the_sql_list(r):
    down = _ok(r, "review_local_down")
    assert down["status"] == "error" and down["error"]["code"] == "local_unavailable"
    due = _ok(r, "due_local_down")
    assert due["status"] == "done" and due["output"]["data"]["contracts"][0]["contract_id"] == "C-0003"
