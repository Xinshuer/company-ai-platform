"""Demo login, session cookie, logout, /api/me per persona, inactive users, security headers, UI."""

from __future__ import annotations

import pytest

from corpai.agents import router
from corpai.api.security import COOKIE, CSP
from tests.api.support import login, owner_exec, owner_rows

pytestmark = pytest.mark.anyio

EXPECTED_APPS = {
    "ceo": {"qa", "legal_review", "report", "sales_profile", "hr_headcount"},
    "sales_staff": {"qa", "sales_profile"},
    "sales_head": {"qa", "report", "sales_profile", "hr_headcount"},
    "eng_staff": {"qa"},
    "legal_staff": {"qa", "legal_review"},
    "hr_staff": {"qa", "hr_headcount", "hr_screening"},
    "platform_admin": {"qa"},
    "auditor": {"qa"},
}


async def test_me_per_persona(app_client, personas):
    for key, p in personas.items():
        await login(app_client, p.user_id)
        r = await app_client.get("/api/me")
        assert r.status_code == 200, (key, r.text)
        me = r.json()
        assert me["user_id"] == p.user_id and me["persona_key"] == key and me["dept"] == str(p.dept)
        assert me["clearance"] == int(p.clearance) and me["role"] == str(p.role)
        apps = {a["id"] for a in me["apps"]}
        if key in EXPECTED_APPS:
            # policy decides who may start an app; /api/me lists only those whose graph exists today
            assert apps == {a for a in EXPECTED_APPS[key] if router.app_ready(a)}, key
        assert all(a["title_zh"] and a["title_en"] for a in me["apps"])
        assert me["can"] == {"approve": str(p.role) in ("manager", "ceo"), "audit": key == "auditor",
                             "admin": key == "platform_admin"}


async def test_session_cookie_flags_and_demo_login_is_audited(app_client, personas):
    p = personas["sales_staff"]
    before = owner_rows("SELECT coalesce(max(seq), 0) AS s FROM audit.log")[0]["s"]
    r = await app_client.post("/api/auth/demo-login", json={"user_id": p.user_id})
    assert r.status_code == 200 and r.json()["persona_key"] == "sales_staff"
    cookie = r.headers["set-cookie"]
    low = cookie.lower()
    assert cookie.startswith(f"{COOKIE}=") and "httponly" in low and "samesite=strict" in low and "path=/" in low
    assert "max-age=28800" in low
    rows = owner_rows("SELECT actor, decision, details FROM audit.log WHERE seq > %s AND event = 'demo_login'",
                      (before,))
    assert rows == [{"actor": p.user_id, "decision": "allow", "details": {"persona_key": "sales_staff"}}]
    assert "eyJ" not in str(owner_rows("SELECT details::text AS d FROM audit.log WHERE seq > %s", (before,)))


async def test_unknown_persona_is_refused_and_audited(app_client):
    r = await app_client.post("/api/auth/demo-login", json={"user_id": "u0999"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    assert COOKIE not in app_client.cookies
    assert owner_rows("SELECT count(*) AS n FROM audit.log WHERE event = 'demo_login' AND decision = 'deny'"
                      " AND reason = 'not_a_persona'")[0]["n"] >= 1


async def test_no_or_forged_cookie_is_unauthenticated(app_client):
    app_client.cookies.clear()
    for method, path in (("GET", "/api/me"), ("POST", "/api/runs"), ("GET", "/api/proposals"),
                         ("GET", "/api/audit"), ("GET", "/api/admin/switches")):
        r = await app_client.request(method, path, json={"app": "qa", "input": "x"} if method == "POST" else None)
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated", path
    app_client.cookies.set(COOKIE, "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ1MDAwMSJ9.")  # alg=none, CEO
    r = await app_client.get("/api/me")
    assert r.status_code == 401


async def test_logout_revokes_the_session(app_client, personas):
    await login(app_client, personas["eng_staff"].user_id)
    token = app_client.cookies[COOKIE]
    r = await app_client.post("/api/auth/logout")
    assert r.status_code == 200 and r.json() == {"ok": True}
    app_client.cookies.clear()
    app_client.cookies.set(COOKIE, token)  # replaying the old cookie after logout
    assert (await app_client.get("/api/me")).status_code == 401


async def test_switching_persona_ends_the_previous_session(app_client, personas):
    await login(app_client, personas["eng_staff"].user_id)
    old = app_client.cookies[COOKIE]
    r = await app_client.post("/api/auth/demo-login", json={"user_id": personas["sales_staff"].user_id})
    assert r.status_code == 200
    app_client.cookies.clear()
    app_client.cookies.set(COOKIE, old)
    assert (await app_client.get("/api/me")).status_code == 401


async def test_deactivated_user_is_rejected_on_the_next_request(app_client, personas):
    p = personas["exec_staff"]
    await login(app_client, p.user_id)
    assert (await app_client.get("/api/me")).status_code == 200
    owner_exec("UPDATE core.users SET active = false WHERE user_id = %s", (p.user_id,))
    try:
        assert (await app_client.get("/api/me")).status_code == 401
        r = await app_client.post("/api/runs", json={"app": "qa", "input": "What does FleetPilot do?"})
        assert r.status_code == 401
        r = await app_client.post("/api/auth/demo-login", json={"user_id": p.user_id})
        assert r.status_code in (400, 403)  # an inactive persona is neither listed nor given a session
    finally:
        owner_exec("UPDATE core.users SET active = true WHERE user_id = %s", (p.user_id,))


async def test_demo_login_switched_off(app_client, personas, monkeypatch):
    from corpai.config import reload_settings

    monkeypatch.setenv("CORPAI_DEMO_LOGIN", "0")
    reload_settings()
    try:
        assert (await app_client.get("/api/personas")).status_code == 404
        r = await app_client.post("/api/auth/demo-login", json={"user_id": personas["ceo"].user_id})
        assert r.status_code == 404 and COOKIE not in r.headers.get("set-cookie", "")
    finally:
        monkeypatch.setenv("CORPAI_DEMO_LOGIN", "1")
        reload_settings()


async def test_personas_and_health(app_client):
    r = await app_client.get("/api/personas")
    assert r.status_code == 200
    rows = r.json()
    assert {"ceo", "sales_staff", "auditor", "platform_admin"} <= {x["persona_key"] for x in rows}
    assert set(rows[0]) == {"user_id", "persona_key", "name", "title", "dept", "role"}
    h = (await app_client.get("/api/health")).json()
    assert h["ok"] is True and h["mode"] == "fake" and h["sim_today"] == "2026-10-01"
    assert set(h["switches"]) == {"local_down", "external_down"}


async def test_security_headers_everywhere(app_client):
    for path in ("/api/health", "/", "/api/me", "/no-such-file.js"):
        r = await app_client.get(path)
        assert r.headers["content-security-policy"] == CSP, path
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
    assert "no-store" in (await app_client.get("/api/health")).headers["cache-control"]
    assert "default-src 'self'" in CSP and "script-src 'self'" in CSP


async def test_root_serves_the_ui(app_client):
    r = await app_client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Content-Security-Policy" in r.text  # ui/index.html, not the placeholder


async def test_placeholder_when_ui_is_missing(tmp_path, monkeypatch):
    import httpx2

    from corpai.api import app as app_module

    monkeypatch.setattr(app_module, "UI_DIR", tmp_path / "no-ui")
    app = app_module.create_app()
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
        r = await c.get("/")
    assert r.status_code == 200 and "UI is not installed" in r.text
    assert r.headers["content-security-policy"] == CSP


async def test_unknown_api_path_uses_the_error_shape(app_client):
    r = await app_client.post("/api/runs", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code in (400, 401)
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}


async def test_me_lists_only_apps_that_can_run(app_client, personas, monkeypatch):
    """Review fix: an app the user may start but whose graph does not exist yet is not advertised
    (every click would answer "arrives in milestone Mx")."""
    from corpai.policy import apps_for

    await login(app_client, personas["ceo"].user_id)
    listed = {a["id"] for a in (await app_client.get("/api/me")).json()["apps"]}
    assert listed == {a for a in apps_for(personas["ceo"]) if router.app_ready(a)}
    assert "qa" in listed
    for app in listed:
        router.resolve(personas["ceo"], app)  # every listed app resolves (no AppNotReady)
    monkeypatch.setattr(router, "app_ready", lambda app: app != "qa")
    listed = {a["id"] for a in (await app_client.get("/api/me")).json()["apps"]}
    assert "qa" not in listed
