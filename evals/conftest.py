"""Shared fixture for the eval suites: the model usage report of a live run.

A live run (CORPAI_LLM=live, eval paths only; see the root conftest.py) ends with one line per
purpose: calls, failed calls, retried calls, tokens and latency, read from the session database's
audit log (event model_call, metadata only, never message text) before the database is dropped.
Fake runs, and live runs that use no database (evals/sales), are unaffected.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest


def usage_summary(rows: list[dict]) -> dict:
    """{"total": {...}, "by_purpose": {purpose: {...}}} from audit model_call rows."""

    def _bucket() -> dict:
        return {"calls": 0, "failed": 0, "retried": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "latency_ms": []}

    total, by_purpose = _bucket(), defaultdict(_bucket)
    for r in rows:
        d = r["details"] or {}
        for b in (total, by_purpose[str(d.get("purpose", "?"))]):
            b["calls"] += 1
            b["failed"] += r["decision"] != "allow"
            b["retried"] += int(d.get("attempts") or 1) > 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                b[k] += int(d.get(k) or 0)
            if d.get("latency_ms") is not None:
                b["latency_ms"].append(int(d["latency_ms"]))

    def _done(b: dict) -> dict:
        lat = sorted(b.pop("latency_ms"))
        b["latency_p50_ms"] = lat[len(lat) // 2] if lat else None
        b["latency_max_ms"] = lat[-1] if lat else None
        return b

    return {"total": _done(total), "by_purpose": {k: _done(v) for k, v in sorted(by_purpose.items())}}


@pytest.fixture(scope="session", autouse=True)
def live_usage_report(request):
    from corpai.config import settings

    if settings().llm_mode != "live" or not any("pg" in item.fixturenames for item in request.session.items):
        yield
        return
    request.getfixturevalue("pg")  # torn down after this fixture: the audit log is still there below
    yield
    from corpai.db import pg as pgmod

    with pgmod.connect_owner() as conn:
        rows = conn.execute("SELECT decision, details FROM audit.log WHERE event = 'model_call'").fetchall()
    report = usage_summary(rows)
    out = request.config.pluginmanager.get_plugin("terminalreporter")
    write = out.write_line if out is not None else print
    write("")
    write(f"live model usage (all): {json.dumps(report['total'])}")
    for purpose, b in report["by_purpose"].items():
        write(f"live model usage ({purpose}): {json.dumps(b)}")
