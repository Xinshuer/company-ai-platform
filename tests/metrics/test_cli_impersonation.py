"""The metrics CLI's --as is a demo login: refused when demo login is off, audited when used."""

from __future__ import annotations

import dataclasses

import pytest

import corpai.config
from corpai.cli import metrics as cli
from tests.metrics.conftest import owner_rows


def test_as_is_refused_when_demo_login_is_off(seeded, monkeypatch):
    """Review finding: `metrics sign --as finance_head` built the CFO's principal with no login and no
    demo_login gate, and the audit log showed the CFO as the one who signed."""
    real = corpai.config.settings()
    monkeypatch.setattr(corpai.config, "settings", lambda: dataclasses.replace(real, demo_login=False))
    before = owner_rows("SELECT count(*) AS n FROM audit.log WHERE action = 'cli_impersonation'")[0]["n"]
    with pytest.raises(cli.ImpersonationRefused):
        cli._principal("finance_head", "sign", "2026-09")
    with pytest.raises(SystemExit):
        cli.main(["sign", "--period", "2026-09", "--as", "finance_head", "--all"])
    after = owner_rows("SELECT count(*) AS n FROM audit.log WHERE action = 'cli_impersonation'")[0]["n"]
    assert after == before


def test_as_in_demo_mode_is_audited_as_a_cli_impersonation(seeded):
    p = cli._principal("finance_head", "sign", "2026-09")
    (row,) = owner_rows("SELECT * FROM audit.log WHERE action = 'cli_impersonation' ORDER BY seq DESC LIMIT 1")
    assert row["event"] == "demo_login" and row["actor"] == p.user_id
    assert row["details"]["via"] == "cli" and row["details"]["command"] == "sign" and row["details"]["os_user"]
