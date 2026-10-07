"""`python -m corpai serve` refuses a non-loopback host while demo login (log in as anyone) is on."""

from __future__ import annotations

import pytest

from corpai.cli import serve


@pytest.mark.parametrize("host,loop", [("127.0.0.1", True), ("localhost", True), ("::1", True), ("[::1]", True),
                                       ("127.0.0.2", True), ("0.0.0.0", False), ("::", False),
                                       ("192.168.1.20", False), ("myhost.example", False)])
def test_is_loopback(host, loop):
    assert serve.is_loopback(host) is loop


@pytest.mark.security
def test_network_host_with_demo_login_is_refused(monkeypatch, capsys):
    from corpai.config import reload_settings

    monkeypatch.setenv("CORPAI_DEMO_LOGIN", "1")
    reload_settings()
    started = []
    monkeypatch.setattr("corpai.api.create_app", lambda: started.append(1))
    try:
        assert serve.main(["--host", "0.0.0.0"]) == 2
        assert started == [] and "demo login" in capsys.readouterr().err
    finally:
        monkeypatch.undo()
        reload_settings()
