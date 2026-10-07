"""python -m corpai synth: argument handling and a real small run."""

from __future__ import annotations

from corpai.cli import synth as cli


def test_cli_small_run_prints_summary(synth_small, capsys):
    assert cli.main(["--small", "--seed", "7"]) == 0
    out = capsys.readouterr().out
    assert "synth seed=7 small=True" in out and "crm.accounts" in out and "overdue_receivable" in out


def test_cli_rejects_bad_seed(capsys):
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["--seed", "not-a-number"])
