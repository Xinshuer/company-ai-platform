"""python -m corpai ingest"""

from __future__ import annotations

from corpai.cli.ingest import main


def test_cli_ingest_reruns_cleanly(seeded, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "0 new version(s)" in out and "chunk(s)" in out


def test_cli_ingest_reports_a_missing_corpus(seeded, tmp_path, capsys):
    assert main(["--corpus-dir", str(tmp_path / "none")]) == 1
    assert "python -m corpai synth" in capsys.readouterr().err


def test_cli_is_discoverable():
    from corpai.__main__ import _commands

    assert "ingest" in _commands()
