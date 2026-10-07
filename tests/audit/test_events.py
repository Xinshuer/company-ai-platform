"""The audit event vocabulary is one list (issues-log D-13): corpai.audit EVENTS, the CHECK on
audit.log.event in the database, and the two migrations that write that CHECK agree, and the human
acts that got their own events (metric sign-off and publication, HR screening and decision) can be
recorded and are refused when misspelt."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from corpai.audit.log import EVENTS
from corpai.db import pg as pgmod

ROOT = Path(__file__).resolve().parents[2]
D13 = {"metric_signed", "metric_published", "hr_screening", "hr_decision"}


def _check_list(sql: str) -> set[str]:
    m = re.search(r"CHECK \(event IN \((.*?)\)\)", sql, flags=re.S)
    assert m, "no CHECK on event found"
    body = re.sub(r"--[^\n]*", "", m.group(1))
    return set(re.findall(r"'([a-z_]+)'", body))


def test_d13_events_exist():
    assert D13 <= EVENTS
    assert "audit_read" in EVENTS


@pytest.mark.parametrize("name", ["001_a1_audit_log.sql", "101_b3_audit_read.sql"])
def test_migrations_list_exactly_the_python_events(name):
    assert _check_list((ROOT / "migrations" / name).read_text(encoding="utf-8")) == EVENTS


def test_database_check_matches_python_events(pg):
    with pgmod.connect_owner() as c:
        defs = [r["d"] for r in c.execute(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conrelid = 'audit.log'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%%owner_job%%'"
        ).fetchall()]
    assert len(defs) == 1
    assert set(re.findall(r"'([a-z_]+)'", defs[0])) == EVENTS


@pytest.mark.parametrize("event", sorted(D13))
def test_new_events_are_recorded(pg, event):
    from corpai import audit

    seq = audit.record(event, actor="u0040", action=f"test.{event}", decision="allow", reason="d13 test")
    with pgmod.connect_owner() as c:
        row = c.execute("SELECT event, action FROM audit.log WHERE seq = %s", (seq,)).fetchone()
    assert row == {"event": event, "action": f"test.{event}"}


def test_unknown_event_refused_in_python_and_sql(pg):
    from corpai import audit

    with pytest.raises(ValueError):
        audit.record("metric_sign", actor="u0040")
    import psycopg

    from corpai.audit.log import _APPEND_SQL

    # the database refuses it too, whoever skips the Python check
    with pytest.raises(psycopg.errors.CheckViolation):
        with pgmod.connect_owner() as c:
            c.execute(_APPEND_SQL, ("metric_sign", "u0040", [], None, None, None, None, None, None, None, None, "{}"))
