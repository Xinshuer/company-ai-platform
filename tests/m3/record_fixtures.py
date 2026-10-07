"""Record ui/fixtures/report.js from the real report graph and metric layer.

    .venv/Scripts/python.exe -m tests.m3.record_fixtures [--small]

Starts its own Postgres under .data/record/<pid>, generates the company (seed 7; full size unless
--small), refreshes 2026-Q3 and 2026-09, signs and publishes 2026-Q3 as the CFO (2026-09 stays draft so
the demo board has something to sign), runs the 2026-Q3 report as each management persona with the
fake LLM, and writes the runs, the board rows (with their draft and published ACLs, so demo_report.js can
filter like row-level security) and the personas' groups. Nothing here calls a real model; the
DeepSeek key is removed from the environment before anything is imported.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["CORPAI_LLM"] = "fake"
os.environ["CORPAI_EMBED"] = "fake"
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ["CORPAI_DATA_DIR"] = str(ROOT / ".data" / "record" / str(os.getpid()))
os.environ["CORPAI_DEMO_LOGIN"] = "1"

import asyncio  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
from decimal import Decimal  # noqa: E402

from corpai.config import reload_settings, settings  # noqa: E402

reload_settings()

PERSONAS = ("ceo", "eng_head", "finance_head", "sales_head", "legal_head", "hr_head")
SIGNED = "2026-Q3"
DRAFT = "2026-09"
INPUT = "2026-Q3 管理层报告"
OUT = ROOT / "ui" / "fixtures" / "report.js"


def _run(coro):
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


async def _report(p) -> dict:
    from corpai.agents import RunContext, Services
    from corpai.agents.report import build_graph, initial_state
    from corpai.gateway import get_gateway
    from corpai.ids import new_id
    from tests.m3.conftest import RecordingTracer

    ctx = RunContext(principal=p, run_id=new_id("run"), thread_id=new_id("thr"), agent="report",
                     services=Services(gateway=get_gateway(), mcp=None, tracer=RecordingTracer()))
    values = await build_graph().compile().ainvoke(initial_state(INPUT), context=ctx)
    events = values.get("events") or []
    route = next((e["route"] for e in reversed(events) if e.get("t") == "route"), None)
    return {"run_id": ctx.run_id, "thread_id": ctx.thread_id, "app": "report", "status": "done",
            "output": values["output"], "hwm": int(values["sensitivity_hwm"]), "route": route, "events": events,
            "pending_approval": None, "error": None}


def _board(period: str) -> dict:
    from corpai.db import pg
    from corpai.metrics import MetricValue, format_value, load_metrics, table_key
    from corpai.types import Level

    defs = load_metrics()
    with pg.connect_owner() as c:
        rows = c.execute(
            "SELECT DISTINCT ON (metric_id, dims_key) * FROM metrics.snapshots WHERE period = %s "
            "ORDER BY metric_id, dims_key, version DESC", (period,)).fetchall()
    out = []
    for r in rows:
        md = defs[r["metric_id"]]
        mv = MetricValue(metric_id=r["metric_id"], period=period, dims=dict(r["dims"] or {}),
                         value=None if r["value"] is None else Decimal(r["value"]), unit=r["unit"],
                         status=r["status"], level=Level(int(md.level)), suppressed=bool(r["suppressed"]),
                         name_zh=md.name_zh, name_en=md.name_en)
        draft_acl = list(r["acl_groups"]) if r["status"] in ("draft", "signed") else list(md.draft_acl)

        def iso(v):
            return v.isoformat() if v is not None else None

        # names, formula and sources are in "defs" once; a row carries what is its own
        out.append({
            "key": table_key(mv.metric_id, mv.dims), "metric_id": mv.metric_id, "dims": dict(mv.dims),
            "value": None if mv.value is None or mv.suppressed else str(mv.value), "unit": mv.unit,
            "status": r["status"], "level": int(md.level), "suppressed": mv.suppressed,
            "display_zh": format_value(mv, "zh"), "display_en": format_value(mv, "en"),
            "provenance": {"sql_sha256": r["sql_sha256"], "version": int(r["version"]), "signed_by": r["signed_by"],
                           "signed_at": iso(r["signed_at"]), "published_at": iso(r["published_at"])},
            "acl_draft": draft_acl, "acl_published": list(r["published_acl"] or []),
        })
    return {"rows": out}


def main(small: bool) -> None:
    from corpai import synth
    from corpai.db import pg
    from corpai.db.migrate import apply_migrations
    from corpai.identity import directory
    from corpai.metrics import load_metrics, publish, refresh_snapshots, sign_off
    from corpai.synth.personas import PERSONAS as IDS

    data_dir = settings().data_dir
    shutil.rmtree(data_dir, ignore_errors=True)
    pg.ensure_server()
    try:
        with pg.connect_owner() as conn:
            apply_migrations(conn)
        synth.run(seed=7, small=small)
        people = {k: directory.get_principal_sync(IDS[k]) for k in IDS}
        refresh_snapshots(SIGNED)
        refresh_snapshots(DRAFT)
        ids = list(load_metrics())
        _run(sign_off(people["finance_head"], SIGNED, ids))
        _run(publish(people["finance_head"], SIGNED, ids))
        runs = [{"persona_key": k, "app": "report", "input": INPUT, "run": _run(_report(people[k]))} for k in PERSONAS]
        defs = [{**md.public_dict(), "draft_acl": list(md.draft_acl)} for md in load_metrics().values()]
        for d in defs:
            d.pop("sql_sha256", None)  # the hash travels with each row's provenance
        fixture = {
            "runs": runs, "traces": {}, "proposals": {"by_persona": {}, "resumed_runs": {}},
            "metrics": {"defs": defs, "periods": {SIGNED: _board(SIGNED), DRAFT: _board(DRAFT)},
                        "groups": {k: sorted(p.groups) for k, p in people.items()}},
        }
        text = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
        if "KB-CANARY-" in text:
            raise SystemExit("a canary reached the fixture; refusing to write it")
        OUT.write_text(f"window.FIXTURES.report = {text};\n", encoding="utf-8", newline="\n")
        print(f"wrote {OUT} ({len(text) // 1024} KiB, {len(runs)} runs)")
    finally:
        pg.stop_server()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main(small="--small" in sys.argv[1:])
