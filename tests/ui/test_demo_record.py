"""`python -m corpai demo-record` (corpai/cli/demo_record.py), run as a subprocess: it sets its own data dir
and model mode, which must not leak into this test session.

- fake mode records a small report fixture into a temporary folder, in the shapes the demo reads (each
  management persona in Chinese and English, the board rows), stamped as a fake recording, and deletes
  its throwaway database;
- live mode without the API key refuses to start, names the variable and writes nothing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("DEEPSEEK_API_KEY", "CORPAI_DATA_DIR")}
    env.update(PYTHONUTF8="1", CORPAI_EMBED="fake", **extra)
    return env


def _record(out: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "corpai", "demo-record", "--out-dir", str(out), *args], cwd=ROOT,
                          env=_env(**env), capture_output=True, text=True, encoding="utf-8", timeout=900)


@pytest.mark.slow
def test_fake_mode_records_the_report_fixture_and_cleans_up(tmp_path):
    from corpai.cli.demo_record import REPORT_INPUTS, REPORT_PERSONAS

    proc = _record(tmp_path, "--small", "--only", "report", CORPAI_LLM="fake")
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "model mode fake" in proc.stdout
    assert [p.name for p in tmp_path.iterdir()] == ["report.js"]
    m = re.fullmatch(r"window\.FIXTURES\.report = (.*);\n", (tmp_path / "report.js").read_text(encoding="utf-8"),
                     flags=re.S)
    fx = json.loads(m.group(1))
    assert fx["recorded"]["mode"] == "fake" and fx["recorded"]["company"] == "small"
    got = {(i["persona_key"], i["input"], i["run"]["output"]["data"]["lang"]) for i in fx["runs"]}
    assert got == {(k, text, lang) for k in REPORT_PERSONAS for lang, text in REPORT_INPUTS.items()}
    assert all(i["run"]["status"] == "done" and i["run"]["route"] == "local" for i in fx["runs"])
    assert set(fx["metrics"]["periods"]) == {"2026-Q3", "2026-09"}
    assert {r["status"] for r in fx["metrics"]["periods"]["2026-Q3"]["rows"]} == {"published"}
    db = re.search(r"database (\S+);", proc.stdout).group(1)
    assert Path(db).parent.name == "record" and not Path(db).exists(), "the throwaway database is deleted"


def test_live_mode_without_the_key_refuses_to_start(tmp_path):
    proc = _record(tmp_path, "--only", "report", CORPAI_LLM="live")
    assert proc.returncode == 2 and "DEEPSEEK_API_KEY" in proc.stderr
    assert not any(tmp_path.iterdir())


def test_only_takes_known_apps(tmp_path):
    proc = _record(tmp_path, "--only", "report,payroll", CORPAI_LLM="fake")
    assert proc.returncode != 0 and "payroll" in proc.stderr and not any(tmp_path.iterdir())


def _shared_run(collections=("general", "public"), tools=(("docs", "search_docs"), ("docs", "get_doc")),
                sources=("rag.chunk:pub-home@v1#0",), cited=("pub-home",), read="pub-home") -> dict:
    events = [{"t": "retrieval", "count": 1, "level": 2, "collections": list(collections)}]
    events += [{"t": "tool", "domain": d, "tool": t, "decision": "allow"} for d, t in tools]
    return {"run_id": "run_x", "read": read, "events": events,
            "output": {"data": {"tools": [{"domain": "docs", "tool": "search_docs", "sources": list(sources)}]},
                       "citations": [{"doc_id": d} for d in cited]}}


@pytest.mark.parametrize("run,why", [
    (_shared_run(), None),
    (_shared_run(collections=("eng", "general", "public")), "searched"),
    (_shared_run(tools=(("docs", "search_docs"), ("code", "search_tickets"))), "called code.search_tickets"),
    (_shared_run(sources=("crm.account:A-0002",)), "record source"),
    (_shared_run(sources=("rag.chunk:eng-postmortem-2026-04-22@v1#0",)), "eng-postmortem-2026-04-22"),
    (_shared_run(cited=("eng-postmortem-2026-04-22",)), "eng-postmortem-2026-04-22"),
    (_shared_run(cited=("crm.account:A-0002",)), "crm.account:A-0002"),
    (_shared_run(sources=("rag.chunk:pub-home@v1#0", "doc:pub-fleetpilot")), None),  # docs.get_doc's source
    (_shared_run(sources=("doc:eng-postmortem-2026-04-22",)), "eng-postmortem-2026-04-22"),
    (_shared_run(read="eng-postmortem-2026-04-22"), "eng-postmortem-2026-04-22"),  # the audit row of a read
])
def test_a_shared_sample_holds_only_what_every_persona_may_read(monkeypatch, run, why):
    """issues-log SEC-1: demo.js serves a "*" recording to every persona; the recorder refuses one that
    searched, called or read beyond the company-wide documents (acl_groups with "all")."""
    from corpai.cli import demo_record

    acl = {"pub-home": ["all"], "pub-fleetpilot": ["all"], "eng-postmortem-2026-04-22": ["dept:eng", "head:eng"]}

    def rows(sql, params=()):
        if "audit.log" in sql:  # the run's retrieval hits (one page beyond what it used) and the get_doc read
            return [{"event": "retrieval", "resource": "rag:qa", "ids": ["pub-fleetpilot"]},
                    {"event": "tool_call", "resource": f"doc:{run['read']}", "ids": None}]
        docs, chunks = params
        ids = set(docs) | {c.split("@", 1)[0] for c in chunks}
        return [{"doc_id": d} for d in sorted(ids) if "all" in acl.get(d, [])]

    monkeypatch.setattr(demo_record, "_owner_rows", rows)
    if why is None:
        demo_record._check_shared(run)
    else:
        with pytest.raises(demo_record.RecordError, match=re.escape(why)):
            demo_record._check_shared(run)
