"""Fixtures for the M3 report wiring tests.

Snapshot periods are shared database state, so this directory owns its own (tests/metrics/conftest.py
owns 2026-08, 2026-09, 2026-Q3 and a few single-test months):
  2026-Q2   refreshed, every metric signed by the CFO, every metric except revenue_recognised published
            (revenue_recognised stays signed-only: tests/metrics and tests/mcp assert that sales staff
            see revenue_recognised for 2026-08 only, and a signed-only metric shows the CEO/CTO
            difference between "signed" and "published")
  2026-07   left to the API tests (refresh, sign and publish through HTTP)
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine

import pytest

REPORT_PERIOD = "2026-Q2"
API_PERIOD = "2026-07"
REPORT_INPUT = "2026-Q2 管理层报告"
UNPUBLISHED = ("revenue_recognised",)
REPORT_PERSONAS = ("ceo", "eng_head", "finance_head", "sales_head", "hr_head", "legal_head")


def run(coro: Coroutine) -> Any:
    """Run a coroutine from sync code on the Selector loop (psycopg async needs it on Windows)."""
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    from corpai.db import pg

    with pg.connect_owner() as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture(scope="session")
def q2(seeded, personas):
    """2026-Q2 signed (all) and published (all but UNPUBLISHED) by the CFO."""
    from corpai.metrics import load_metrics, publish, refresh_snapshots, sign_off

    refresh_snapshots(REPORT_PERIOD)
    ids = list(load_metrics())
    cfo = personas["finance_head"]
    run(sign_off(cfo, REPORT_PERIOD, ids))
    run(publish(cfo, REPORT_PERIOD, [m for m in ids if m not in UNPUBLISHED]))
    return REPORT_PERIOD


class RecordingTracer:
    """Keeps span names, kinds, levels and attrs in memory (the graph tests need no trace table)."""

    def __init__(self) -> None:
        self.spans: list[dict] = []

    def span(self, name, *, kind, run_id, principal, level, parent_id=None, input=None):
        from contextlib import asynccontextmanager

        from corpai.types import Level

        rec = {"name": name, "kind": kind, "level": int(level), "attrs": {}, "input": input}
        self.spans.append(rec)

        class _Span:
            span_id = f"span_{len(self.spans)}"

            def set_level(self, lv):
                rec["level"] = max(rec["level"], int(Level(int(lv))))

            def set_attr(self, k, v):
                rec["attrs"][k] = v

            def set_output(self, text):
                rec["output"] = text

        @asynccontextmanager
        async def cm():
            yield _Span()

        return cm()


async def run_report(p, text: str = REPORT_INPUT, *, user_level: int = 1, tracer: Any = None) -> dict:
    """Run the report graph directly (no checkpointer) as `p`; returns the final state values."""
    from corpai.agents import RunContext, Services
    from corpai.agents.report import build_graph, initial_state
    from corpai.gateway import get_gateway
    from corpai.ids import new_id
    from corpai.types import Level

    ctx = RunContext(principal=p, run_id=new_id("run"), thread_id=new_id("thr"), agent="report",
                     services=Services(gateway=get_gateway(), mcp=None, tracer=tracer or RecordingTracer()),
                     user_level=Level(user_level))
    return await build_graph().compile().ainvoke(initial_state(text), context=ctx)


@pytest.fixture(scope="session")
def reports(q2, personas) -> dict[str, dict]:
    """The 2026-Q2 report of each management persona, run once per session."""
    return {k: run(run_report(personas[k])) for k in REPORT_PERSONAS}


@pytest.fixture(scope="session")
def planted(seeded) -> dict[str, dict]:
    """The evaluation answers (owner read; the app can never read synth.*)."""
    return {r["kind"]: {"ref": r["ref"], **r["expected"]}
            for r in owner_rows("SELECT kind, ref, expected FROM synth.planted")}
