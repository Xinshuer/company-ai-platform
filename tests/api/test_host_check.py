"""DNS rebinding and cross-site writes (issues-log FN-26): the API answers loopback host names (and the
configured ones) only, and refuses a state-changing request whose Origin is another site."""

from __future__ import annotations

import httpx2
import pytest

from corpai.api.security import HostCheck, host_name

pytestmark = pytest.mark.anyio


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


async def _send(app, method: str, host: str, origin: str | None = None) -> httpx2.Response:
    headers = {"host": host} | ({"origin": origin} if origin else {})
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000") as c:
        return await c.request(method, "/api/auth/demo-login", headers=headers)


@pytest.mark.parametrize("netloc,name", [("127.0.0.1:8000", "127.0.0.1"), ("LOCALHOST", "localhost"),
                                         ("[::1]:8000", "::1"), ("evil.example:8000", "evil.example"),
                                         ("[::1", ""), ("", "")])
def test_host_name(netloc, name):
    assert host_name(netloc) == name


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000", "127.0.0.1"])
async def test_loopback_hosts_are_served(host):
    assert (await _send(HostCheck(_ok), "POST", host)).status_code == 200


@pytest.mark.parametrize("host", ["evil.example:8000", "evil.example", "127.0.0.1.evil.example", "localhost.evil.example",
                                  "0.0.0.0:8000", ""])
async def test_a_rebound_or_unknown_host_is_refused(host):
    for method in ("GET", "POST"):
        r = await _send(HostCheck(_ok), method, host)
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", (host, method)


async def test_configured_hosts_are_served():
    app = HostCheck(_ok, allowed=["corpai.internal.example", " "])
    assert (await _send(app, "GET", "corpai.internal.example:8443")).status_code == 200
    assert (await _send(app, "GET", "other.internal.example")).status_code == 400


@pytest.mark.parametrize("origin,status", [("http://evil.example", 403), ("null", 403),
                                           ("http://localhost:8000", 403),  # another origin, same machine
                                           ("http://127.0.0.1:8000", 200)])
async def test_a_write_from_another_origin_is_refused(origin, status):
    r = await _send(HostCheck(_ok), "POST", "127.0.0.1:8000", origin)
    assert r.status_code == status, origin
    if status == 403:
        assert r.json()["error"]["code"] == "forbidden"
    # reads stay possible (a cross-origin page cannot read the response anyway: no CORS headers)
    assert (await _send(HostCheck(_ok), "GET", "127.0.0.1:8000", origin)).status_code == 200


async def test_demo_login_through_a_rebound_name_or_from_another_site_is_refused(app_client, personas):
    body = {"user_id": personas["ceo"].user_id}
    r = await app_client.post("/api/auth/demo-login", json=body, headers={"host": "evil.example:8000"})
    assert r.status_code == 400 and "set-cookie" not in r.headers
    assert r.headers["x-frame-options"] == "DENY"  # the refusal still carries the security headers
    r = await app_client.post("/api/auth/demo-login", json=body, headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and "set-cookie" not in r.headers
    r = await app_client.post("/api/auth/demo-login", json=body, headers={"origin": "http://127.0.0.1:8000"})
    assert r.status_code == 200 and "set-cookie" in r.headers
