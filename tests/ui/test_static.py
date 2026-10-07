"""Static checks of the demo UI (architecture §14, decision D9, §18 payloads). Pure Python, no browser.

What these prove:
- the page obeys its CSP by construction: only classic, local scripts; no inline script or handlers;
  no external URL, font, stylesheet or image; no HTML sinks for data (innerHTML, eval);
- the CSP meta equals the spec string;
- zh and en strings have the same keys and every literal key the code uses exists;
- fixtures are strict JSON in the §18 shapes, agree with the policy (apps, approval rights) and
  with the gateway's routing rule, and carry no secrets, canaries or non-fictional domains;
- every view the router references exists in index.html.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui"
INDEX = UI / "index.html"
JS_DIR = UI / "js"
FIX_DIR = UI / "fixtures"

SPEC_CSP = ("default-src 'self'; img-src 'self' data:; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self' http://127.0.0.1:8000")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _js_files() -> list[Path]:
    return sorted(JS_DIR.glob("*.js"))


def _fixture_files() -> list[Path]:
    return sorted(FIX_DIR.glob("*.js"))


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[dict[str, str | None]] = []
        self.script_bodies: list[str] = []
        self.metas: list[dict[str, str | None]] = []
        self.links: list[dict[str, str | None]] = []
        self.ids: set[str] = set()
        self.view_ids: set[str] = set()
        self.handler_attrs: list[tuple[str, str]] = []
        self.styles = 0
        self.i18n_keys: set[str] = set()
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k, _ in attrs:
            if k.lower().startswith("on"):
                self.handler_attrs.append((tag, k))
        if "id" in a and a["id"]:
            self.ids.add(a["id"])
            if tag == "section" and "view" in (a.get("class") or "").split():
                self.view_ids.add(a["id"])
        for k in ("data-i18n", "data-i18n-aria", "data-i18n-placeholder"):
            if a.get(k):
                self.i18n_keys.add(a[k])
        if tag == "script":
            self.scripts.append(a)
            self._in_script = True
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "style":
            self.styles += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.script_bodies.append(data)


@pytest.fixture(scope="module")
def page() -> _Page:
    p = _Page()
    p.feed(_read(INDEX))
    return p


def _load_fixture(path: Path) -> tuple[str, object]:
    src = _read(path)
    m = re.fullmatch(r"window\.FIXTURES\.([A-Za-z_][A-Za-z0-9_]*) = (.*);\n?", src, flags=re.S)
    assert m, f"{path.name}: must be exactly `window.FIXTURES.<name> = <json>;`"
    return m.group(1), json.loads(m.group(2))


@pytest.fixture(scope="module")
def fixtures() -> dict[str, object]:
    out = {}
    for f in _fixture_files():
        name, data = _load_fixture(f)
        out[name] = data
    return out


@pytest.fixture(scope="module")
def i18n() -> dict[str, dict[str, str]]:
    src = _read(JS_DIR / "i18n.js")
    start = src.index("{", src.index("window.I18N ="))
    end = src.rindex("}")
    tail = src[end + 1:].strip()
    assert tail == ";", "i18n.js must end with the JSON literal"
    return json.loads(src[start:end + 1])


# ---------------------------------------------------------------- scripts and CSP

def test_every_script_is_classic_local_and_exists(page):
    assert page.scripts, "no scripts found"
    for s in page.scripts:
        src = s.get("src")
        assert src, "inline <script> is forbidden by script-src 'self'"
        assert (s.get("type") or "text/javascript") in ("text/javascript",), f"{src}: classic scripts only"
        assert "nomodule" not in s
        assert not re.match(r"^[a-z][a-z0-9+.-]*:|^//", src, flags=re.I), f"{src}: must be a relative local path"
        assert not src.startswith("/"), f"{src}: relative paths keep file:// working"
        assert (UI / src).is_file(), f"{src}: missing"
    assert not page.script_bodies, "inline script content is forbidden"


def test_no_inline_event_handlers_or_style_blocks_needing_more_csp(page):
    assert page.handler_attrs == [], "inline handlers need 'unsafe-inline' in script-src"


def test_csp_meta_equals_spec(page):
    csp = [m for m in page.metas if (m.get("http-equiv") or "").lower() == "content-security-policy"]
    assert len(csp) == 1
    assert csp[0].get("content") == SPEC_CSP


def test_spec_csp_constant_matches_architecture():
    arch = _read(ROOT / "docs" / "architecture.md")
    m = re.search(r"CSP meta: `([^`]+)`", arch)
    assert m and m.group(1) == SPEC_CSP


def test_csp_meta_comes_before_any_script_or_stylesheet():
    html = _read(INDEX)
    pos_csp = html.index('http-equiv="Content-Security-Policy"')
    assert pos_csp < html.index("<script") and pos_csp < html.index("<link")


def test_all_js_files_are_loaded_and_ordered(page):
    srcs = [s["src"] for s in page.scripts]
    loaded = {s for s in srcs if s.startswith("js/")}
    on_disk = {f"js/{f.name}" for f in _js_files()}
    assert loaded == on_disk, f"loaded vs on disk differ: {loaded ^ on_disk}"
    # The fixtures are not static tags: js/fixtures_loader.js adds them in demo mode only, so a page
    # served by the live API never requests the recorded (possibly confidential) sample answers.
    assert not any(s.startswith("fixtures/") for s in srcs)
    loader = _read(JS_DIR / "fixtures_loader.js")
    names = json.loads("[" + re.search(r"var NAMES = \[([^\]]*)\];", loader).group(1) + "]")
    assert sorted(f"{n}.js" for n in names) == [f.name for f in _fixture_files()]
    assert srcs.index("js/fixtures_base.js") < srcs.index("js/fixtures_loader.js") < srcs.index("js/demo.js")
    assert srcs.index("js/i18n.js") < srcs.index("js/lang.js")
    assert srcs.index("js/util.js") < srcs.index("js/demo.js") < srcs.index("js/api.js")
    assert srcs[-1] == "js/app.js"


def test_stylesheets_are_local(page):
    for link in page.links:
        href = link.get("href") or ""
        assert not re.match(r"^[a-z][a-z0-9+.-]*:|^//", href, flags=re.I), href
        assert (UI / href).is_file()


# ---------------------------------------------------------------- no external URLs, no HTML sinks

_URL = re.compile(r"""(?:https?|wss?|ftp)://[^\s'"`)<>]+|(?<![:\w])//[a-z0-9.-]+\.[a-z]{2,}""", re.I)
# The only literal URLs allowed in code: the SVG namespace (not fetched) and the CSP's own origin.
_ALLOWED_URLS = {"http://www.w3.org/2000/svg", "http://127.0.0.1:8000"}


def _code_without_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


@pytest.mark.parametrize("path", [INDEX, UI / "css" / "app.css", *_js_files()], ids=lambda p: p.name)
def test_no_external_urls_in_html_css_js(path):
    src = _read(path)
    found = {u.rstrip(".,;") for u in _URL.findall(src)}
    assert found <= _ALLOWED_URLS, f"{path.name}: external URLs {found - _ALLOWED_URLS}"


def test_css_loads_nothing_external():
    css = _read(UI / "css" / "app.css")
    assert "@import" not in css
    assert "@font-face" not in css, "system font stack only"
    for u in re.findall(r"url\(([^)]*)\)", css):
        assert u.strip("'\" ").startswith("data:"), u


@pytest.mark.parametrize("path", _js_files(), ids=lambda p: p.name)
def test_js_has_no_html_sinks_modules_or_eval(path):
    code = _code_without_comments(_read(path))
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                "setTimeout(\"", "setTimeout('", "import(", "XMLHttpRequest"):
        assert bad not in code, f"{path.name}: {bad}"
    assert not re.search(r"(?m)^\s*(import|export)\s", code), f"{path.name}: ES module syntax"


def test_fetch_only_targets_same_origin_api():
    for path in _js_files():
        code = _code_without_comments(_read(path))
        for call in re.findall(r"fetch\(([^,)]*)", code):
            arg = call.strip()
            # either a literal /api/... path or the `path` variable of the one request helper in api.js
            assert arg.startswith('"/api/') or (path.name == "api.js" and arg == "path"), f"{path.name}: fetch({arg})"
        if "fetch(" in code:
            assert path.name == "api.js", "only api.js talks to the network"
    api = _read(JS_DIR / "api.js")
    assert 'credentials: "same-origin"' in api
    assert "localStorage" not in _code_without_comments(api).replace("U.store", "")


def test_no_token_handling_in_browser_code():
    # the session cookie is HttpOnly: page code never reads cookies or keeps tokens
    for path in _js_files():
        code = _code_without_comments(_read(path))
        assert "document.cookie" not in code, path.name
        assert not re.search(r"Authorization|Bearer|api_key", code, flags=re.I), path.name
        assert "DEEPSEEK_API_KEY" not in code, path.name


def test_external_links_confirm_and_show_full_url():
    util = _read(JS_DIR / "util.js")
    body = util[util.index("U.extLink"):util.index("U.linkify")]
    assert "window.confirm" in body and "preventDefault" in body
    assert "noopener" in body and "noreferrer" in body
    assert 'class: "ext-url mono", text: url' in body, "the full URL is always shown"
    assert "isSafeHttpUrl" in body, "only http(s) links become clickable"


def test_lf_line_endings():
    for p in [INDEX, UI / "css" / "app.css", *_js_files(), *_fixture_files()]:
        assert b"\r\n" not in p.read_bytes(), p.name


# ---------------------------------------------------------------- i18n

def test_i18n_zh_en_identical_keys_and_non_empty(i18n):
    assert set(i18n) == {"zh", "en"}
    assert set(i18n["zh"]) == set(i18n["en"])
    for lang in ("zh", "en"):
        empty = [k for k, v in i18n[lang].items() if not isinstance(v, str) or not v.strip()]
        assert not empty, f"{lang}: empty strings {empty}"


def test_i18n_placeholders_match_between_languages(i18n):
    for k, zh in i18n["zh"].items():
        assert set(re.findall(r"\{(\w+)\}", zh)) == set(re.findall(r"\{(\w+)\}", i18n["en"][k])), k


def test_every_literal_i18n_key_exists(i18n, page):
    keys = set(i18n["zh"])
    used: set[str] = set(page.i18n_keys)
    for path in _js_files():
        code = _read(path)
        # literal keys only: t("x_" + y) builds a dynamic family, checked in the next test
        used |= set(re.findall(r"\bt\(\s*\"([a-z][a-z0-9_]*)\"\s*[,)]", code))
        used |= set(re.findall(r"\blabel:\s*\"([a-z][a-z0-9_]*)\"", code))
        used |= {k for m in re.findall(r"pageHeader\(\"[^\"]+\",\s*\"([a-z0-9_]+)\"(?:,\s*\"([a-z0-9_]+)\")?", code)
                 for k in m if k}
    used.discard("key")  # the doc comment in lang.js
    assert used - keys == set()


def test_dynamic_i18n_families_are_complete(i18n):
    keys = set(i18n["zh"])
    fams = {
        "level_{}": ["1", "2", "3", "4"], "level_{}_hint": ["1", "2", "3", "4"],
        "route_{}": ["local", "external", "none"],
        "dept_{}": ["exec", "sales", "eng", "legal", "finance", "hr", "it"],
        "role_{}": ["employee", "manager", "ceo", "platform_admin", "security_auditor"],
        "evt_{}": ["classify", "retrieval", "tool", "hwm", "route", "approval", "notice"],
        "status_{}": ["done", "waiting_approval", "error", "pending", "approved", "rejected", "executed", "failed", "expired"],
        "decision_{}": ["allow", "deny", "error", "reroute"],
        "tier_{}": ["1", "2", "3"],
        "err_{}": ["unauthenticated", "forbidden", "not_found", "bad_request", "local_unavailable",
                   "external_unavailable", "gateway_busy", "budget_exceeded", "secret_detected", "network",
                   "demo_not_recorded", "unknown"],
        "kind_{}": ["node", "llm", "tool", "retrieval", "policy"],
        "req_{}": ["met", "not_met", "unknown"],
        "sw_{}": ["local_down", "external_down"], "sw_{}_does": ["local_down", "external_down"],
        "sw_{}_never": ["local_down", "external_down"],
        "mode_reason_{}": ["file", "forced", "health", "no_backend"],
        "app_{}": ["qa", "legal_review", "report", "sales_profile", "hr_headcount", "hr_screening"],
        "app_{}_desc": ["qa", "legal_review", "report", "sales_profile", "hr_headcount", "hr_screening"],
    }
    missing = [f.format(v) for f, vals in fams.items() for v in vals if f.format(v) not in keys]
    for app, n in (("legal_review", 4), ("report", 4), ("sales_profile", 4), ("hr_screening", 4), ("hr_headcount", 3)):
        for suffix in ["_who", "_safety", "_input"] + [f"_step{i}" for i in range(1, n + 1)]:
            if f"app_{app}{suffix}" not in keys:
                missing.append(f"app_{app}{suffix}")
    assert not missing


def test_local_banner_and_metadata_only_strings(i18n):
    assert i18n["zh"]["local_banner"] == "本会话已切换到本地模式"
    assert i18n["zh"]["route_local"] == "本地" and i18n["zh"]["route_external"] == "外部"
    assert "仅元数据" in i18n["zh"]["metadata_only"] and "metadata only" in i18n["zh"]["metadata_only"]
    assert i18n["zh"]["nav_compare"] == "同一问题，不同的人"
    assert "DeepSeek" in i18n["zh"]["footer_disclaimer"] and "DeepSeek" in i18n["en"]["footer_disclaimer"]


# ---------------------------------------------------------------- router

def test_every_router_view_exists(page):
    router = _read(JS_DIR / "router.js")
    views = re.findall(r'view:\s*"(view-[a-z0-9-]+)"', router)
    assert views, "router lists no views"
    missing = [v for v in views if v not in page.ids]
    assert not missing
    assert page.view_ids == set(views), "every <section class=view> is reachable and vice versa"


def test_every_route_has_a_view_renderer():
    router = _read(JS_DIR / "router.js")
    paths = re.findall(r'\{path:\s*"([a-z]+)"', router)
    all_js = "\n".join(_read(p) for p in _js_files())
    app_routes = set(re.findall(r'^\s{4}([a-z]+): \{app: "', _read(JS_DIR / "view_apps.js"), flags=re.M))
    for p in paths:
        assert f"KB.views.{p} =" in all_js or p in app_routes, f"route {p} has no renderer"


def test_router_gates_ops_pages_and_apps():
    router = _read(JS_DIR / "router.js")
    assert re.search(r'path: "audit".*need: "audit"', router)
    assert re.search(r'path: "admin".*need: "admin"', router)
    # pages also refuse on their own (explicit denial instead of a hidden link only)
    assert "me.can.audit" in _read(JS_DIR / "view_audit.js")
    assert "me.can.admin" in _read(JS_DIR / "view_admin.js")
    assert "C.denied(" in _read(JS_DIR / "view_apps.js")


# ---------------------------------------------------------------- fixtures

RUN_KEYS = {"run_id", "thread_id", "app", "status", "output", "hwm", "route", "events", "pending_approval", "error"}
ERROR_CODES = {"unauthenticated", "forbidden", "not_found", "bad_request", "local_unavailable",
               "external_unavailable", "gateway_busy", "budget_exceeded", "secret_detected"}
EVENT_TYPES = {"classify", "retrieval", "tool", "hwm", "route", "approval", "notice"}
PROPOSAL_KEYS = {"proposal_id", "created_at", "requester", "agent", "domain", "tool", "tier", "status", "summary",
                 "params", "params_hash", "can_approve", "why_not"}


def test_fixture_files_parse_and_names_match(fixtures):
    for f in _fixture_files():
        name, _ = _load_fixture(f)
        assert name == f.stem
    required = {"personas", "me", "health", "switches", "runs", "compare", "proposals", "traces", "audit"}
    assert required <= set(fixtures)


def test_personas_fixture_matches_synth(fixtures):
    from corpai.synth.personas import PERSONA_SPECS

    got = fixtures["personas"]["personas"]
    assert len(got) == 14
    by_key = {p["persona_key"]: p for p in got}
    for s in PERSONA_SPECS:
        p = by_key[s.key]
        assert set(p) == {"user_id", "persona_key", "name", "title", "dept", "role"}
        assert (p["user_id"], p["name"], p["title"], p["dept"], p["role"]) == (s.user_id, s.name, s.title, s.dept, s.role)


def _principal(spec):
    from corpai.types import Dept, Level, Principal, Role

    return Principal(user_id=spec.user_id, name=spec.name, email=f"{spec.user_id}@kestrelbyte.example",
                     dept=Dept(spec.dept), role=Role(spec.role), title=spec.title, manager_id=spec.manager_id,
                     clearance=Level.L3, groups=frozenset({"all"}))


def test_me_fixture_agrees_with_policy(fixtures):
    from corpai.policy import apps_for
    from corpai.synth.personas import PERSONA_SPECS

    me = fixtures["me"]["by_persona"]
    assert set(me) == {s.key for s in PERSONA_SPECS}
    for s in PERSONA_SPECS:
        m = me[s.key]
        assert {"user_id", "name", "title", "dept", "role", "clearance", "persona_key", "apps", "can"} <= set(m)
        assert [a["id"] for a in m["apps"]] == apps_for(_principal(s)), s.key
        for a in m["apps"]:
            assert set(a) == {"id", "title_zh", "title_en"}
        assert set(m["can"]) == {"approve", "audit", "admin"}
        assert m["can"]["audit"] == (s.role == "security_auditor")
        assert m["can"]["admin"] == (s.role == "platform_admin")
        assert m["can"]["approve"] == (s.role in ("manager", "ceo"))
        # §5.1 clearance
        expect = 4 if s.role in ("ceo", "manager") or s.dept in ("legal", "finance", "hr") else 3
        if s.role in ("platform_admin", "security_auditor"):
            expect = 2
        assert m["clearance"] == expect, s.key


def _all_runs(fixtures):
    for item in fixtures["runs"]["items"]:
        yield item, item["run"]
    for run in fixtures["proposals"].get("resumed_runs", {}).values():
        yield None, run


def test_runs_have_section_18_shape(fixtures):
    for item, run in _all_runs(fixtures):
        assert set(run) == RUN_KEYS, run.get("run_id")
        assert run["status"] in ("done", "waiting_approval", "error")
        assert run["route"] in ("local", "external")
        assert run["hwm"] in (1, 2, 3, 4)
        assert set(run["output"]) == {"text", "citations", "data"}
        for c in run["output"]["citations"]:
            assert set(c) == {"doc_id", "title", "level", "collection", "snippet"}
        for e in run["events"]:
            assert e["t"] in EVENT_TYPES, e
        if run["status"] == "error":
            assert run["error"]["code"] in ERROR_CODES and run["error"]["message"]
        else:
            assert run["error"] is None
        if run["status"] == "waiting_approval":
            assert run["pending_approval"]["tier"] in (2, 3)
        if item is not None:
            assert item["app"] == run["app"]


def test_runs_obey_routing_and_level_rules(fixtures):
    """Fixtures must not teach the wrong thing: L3+ never on external, levels only go up."""
    for _, run in _all_runs(fixtures):
        if run["hwm"] >= 3:
            assert run["route"] == "local", run["run_id"]
        for c in run["output"]["citations"]:
            assert c["level"] <= run["hwm"], (run["run_id"], c["doc_id"])
            if c["level"] >= 4:
                assert c["snippet"] is None, "L4 excerpts never reach the UI"
        for e in run["events"]:
            if e["t"] == "hwm":
                assert e["to"] > e["from"]
            if e["t"] == "route" and e["level"] >= 3:
                assert e["route"] == "local"
        for e in run["events"]:
            if e["t"] == "notice" and e.get("code") == "local_mode":
                assert run["route"] == "local" and e.get("zh") and e.get("en")


def test_required_demo_scenarios_present(fixtures):
    items = fixtures["runs"]["items"]
    public = [i for i in items if i["app"] == "qa" and i["run"]["route"] == "external" and i["run"]["hwm"] == 1]
    assert public, "a public Q&A run on the external route"
    sales = [i for i in items if i["persona_key"] == "sales_staff" and i["app"] == "qa" and not i.get("switches")
             and i["run"]["route"] == "local" and i["run"]["hwm"] == 3
             and any(e["t"] == "tool" and e["domain"] == "crm" for e in i["run"]["events"])]
    assert sales, "internal Q&A for sales_staff (local, L3, CRM tool event)"
    down = [i for i in items if (i.get("switches") or {}).get("local_down")]
    assert down and all(i["run"]["status"] == "error" and i["run"]["error"]["code"] == "local_unavailable"
                        and i["run"]["route"] == "local" for i in down)
    for app in ("legal_review", "report", "sales_profile", "hr_screening", "hr_headcount"):
        assert any(i["app"] == app for i in items), f"sample run for {app}"


def test_compare_fixture_shows_different_citations(fixtures):
    cmp_ = fixtures["compare"]
    assert cmp_["personas"] == ["sales_staff", "eng_staff", "ceo"]
    runs = {}
    for i in fixtures["runs"]["items"]:
        if i["input"] == cmp_["question"] and i["app"] == cmp_["app"] and not i.get("switches"):
            runs[i["persona_key"]] = i["run"]
    assert set(cmp_["personas"]) <= set(runs)
    cits = [frozenset(c["doc_id"] for c in runs[k]["output"]["citations"]) for k in cmp_["personas"]]
    assert len(set(cits)) == 3, "each person sees different citations"
    # the engineer may not use CRM tools: the denial is explicit in the events
    assert any(e["t"] == "tool" and e["domain"] == "crm" and e["decision"] == "deny"
               for e in runs["eng_staff"]["events"])


def test_proposals_fixture_shapes_and_four_eyes(fixtures):
    me = fixtures["me"]["by_persona"]
    boxes = fixtures["proposals"]["by_persona"]
    seen_pending_requester = seen_pending_head = seen_done = False
    for key, b in boxes.items():
        assert set(b) == {"mine", "to_approve"}
        uid = me[key]["user_id"]
        for box, plist in b.items():
            for p in plist:
                assert PROPOSAL_KEYS <= set(p), p["proposal_id"]
                assert re.fullmatch(r"[0-9a-f]{64}", p["params_hash"])
                assert p["tier"] in (2, 3) and p["params"]
                if box == "to_approve":
                    assert me[key]["can"]["approve"], f"{key} has a to_approve box without approve rights"
                if p["tier"] == 3 and p["requester"]["user_id"] == uid:
                    assert p["can_approve"] is False and p["why_not"], "tier 3 self-approval must be refused"
                if p["can_approve"]:
                    assert p["status"] == "pending"
                if p["tool"] == "send_email" and p["tier"] == 3 and p["status"] == "pending":
                    if p["requester"]["user_id"] == uid:
                        seen_pending_requester = True
                    elif key == "sales_head" and p["can_approve"]:
                        seen_pending_head = True
                if p["status"] in ("approved", "executed"):
                    seen_done = True
    assert seen_pending_requester and seen_pending_head and seen_done
    # the pending proposal shows identical params and hash to requester and approver
    pend = {p["proposal_id"]: p for b in boxes.values() for lst in b.values() for p in lst if p["status"] == "pending"}
    for b in boxes.values():
        for lst in b.values():
            for p in lst:
                if p["proposal_id"] in pend:
                    assert p["params"] == pend[p["proposal_id"]]["params"]
                    assert p["params_hash"] == pend[p["proposal_id"]]["params_hash"]


def test_traces_fixture_masks_l4(fixtures):
    run_ids = {r["run_id"] for _, r in _all_runs(fixtures)}
    for rid, tr in fixtures["traces"]["by_run"].items():
        assert tr["run_id"] == rid and rid in run_ids
        ids = {s["span_id"] for s in tr["spans"]}
        for s in tr["spans"]:
            assert {"span_id", "parent_id", "name", "kind", "level", "status", "started_at", "ended_at", "attrs",
                    "input_text", "output_text"} <= set(s)
            assert s["kind"] in ("node", "llm", "tool", "retrieval", "policy")
            assert s["parent_id"] is None or s["parent_id"] in ids
            if s["level"] >= 4:
                assert s["input_text"] is None and s["output_text"] is None, "L4 spans carry no text"
            if s["kind"] == "llm" and s["level"] >= 3:
                assert s["attrs"].get("route") == "local"


def test_audit_fixture_shape(fixtures):
    a = fixtures["audit"]
    assert set(a["chain"]) == {"ok", "checked", "first_bad_seq"}
    keys = {"seq", "ts", "actor", "act_chain", "event", "action", "resource", "decision", "reason", "route", "level",
            "approval_id", "run_id", "details"}
    seqs = [e["seq"] for e in a["entries"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    for e in a["entries"]:
        assert set(e) == keys
        assert e["route"] in (None, "local", "external")
    assert any(e["event"] == "audit_read" for e in a["entries"])


_CRED_KEY = re.compile(r"(?i)(^|_)(token|authorization|api_key|password|passwd|secret|cookie|session|bearer|jwt|"
                       r"credentials?|private_key)($|_)")


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + "/" + k, k, None
            yield from _walk(v, path + "/" + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, None, obj


def test_fixtures_carry_no_secrets_canaries_or_real_domains(fixtures):
    for name, data in fixtures.items():
        for path, key, val in _walk(data):
            if key is not None and key not in ("prompt_tokens", "completion_tokens"):
                assert not _CRED_KEY.search(key), f"{name}{path}: credential-like key"
            if val is None:
                continue
            assert "KB-CANARY-" not in val, f"{name}{path}: canary string in a fixture"
            assert not re.search(r"sk-[A-Za-z0-9]{8,}|Bearer\s|eyJ[A-Za-z0-9_-]{10,}\.|BEGIN [A-Z ]*PRIVATE KEY|DEEPSEEK",
                                 val), f"{name}{path}: secret-like value"
            for host in re.findall(r"https?://([^/\s:]+)", val):
                assert host.endswith(".example"), f"{name}{path}: non-fictional host {host}"
            for dom in re.findall(r"[\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", val):
                assert dom.endswith(".example"), f"{name}{path}: non-fictional email domain {dom}"


# ---------------------------------------------------------------- review fixes (static guards)

def test_live_mode_never_loads_fixtures_and_has_no_force_demo():
    api = _code_without_comments(_read(JS_DIR / "api.js"))
    app = _code_without_comments(_read(JS_DIR / "app.js"))
    assert api.count("KB.fixtures.load()") == 1, "fixtures are loaded in one place: the demo branch"
    demo_fn = api[api.index("function demo(reason)"):api.index("A.init = function")]
    assert "KB.fixtures.load()" in demo_fn
    for code in (api, app):
        assert "forcedDemo" not in code and "setForcedDemo" not in code
    assert 'id="btn-mode"' not in _read(INDEX)


def test_narrow_layout_keeps_spare_height_out_of_the_nav_row():
    css = _read(UI / "css" / "app.css")
    block = css[css.index("@media (max-width: 900px)"):]
    shell = re.search(r"\.shell \{([^}]*)\}", block).group(1)
    assert "align-content: start" in shell


def test_banner_refreshes_switches_from_the_server():
    app = _code_without_comments(_read(JS_DIR / "app.js"))
    rerender = app[app.index("function rerender()"):app.index("function boot()")]
    assert "A.refreshBanner()" in rerender
    assert "KB.api.noteSwitches(switches)" in app


def test_compare_rechecks_identity_when_switching_back_fails():
    cmp_ = _code_without_comments(_read(JS_DIR / "view_compare.js"))
    assert "KB.app.loadMe()" in cmp_ and "cmp_relogin_failed" in cmp_


def test_approve_toast_follows_the_execution_status():
    code = _code_without_comments(_read(JS_DIR / "view_approvals.js"))
    assert "approved_failed_toast" in code and 'st === "executed"' in code
    # first load and reloads share one path (empty state included) and keep the result on screen
    assert "proposalCard(p, showResult)" in code and "proposalCard(q, function () { KB.router.refresh(); })" not in code


def test_zh_labels_read_naturally(i18n):
    zh = i18n["zh"]
    assert zh["nav_trace"] == "运行追踪"
    assert zh["run_id"] == zh["trace_run_id"] == "运行 ID"
    assert zh["trace_spans"] == "span 数量"
