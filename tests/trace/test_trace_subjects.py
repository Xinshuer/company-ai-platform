"""GDPR data subjects on trace spans (migration 103; issues-log D-04): a span records whose rows it read,
subjects only accumulate, only row ids are accepted, no reader returns them, and trace.erase_subject()
(owner only) blanks the text of every run in the threads that touched the person."""

from __future__ import annotations

import psycopg
import pytest

from corpai.db import pg as pgmod
from corpai.ids import new_id
from corpai.trace import Tracer, read_run
from corpai.trace.span import MAX_SUBJECTS
from corpai.types import Level

pytestmark = pytest.mark.anyio


def _keep(text: str, level: Level) -> str | None:
    return text


@pytest.fixture
def tracer() -> Tracer:
    return Tracer(masker=_keep)


@pytest.fixture
def alice(mkp):
    return mkp("u0031")


def _owner(sql: str, params: tuple = ()) -> list[dict]:
    with pgmod.connect_owner() as c:
        return c.execute(sql, params).fetchall()


async def _span(tracer, p, run_id, *, subjects=(), kind="tool", text="Contact Erika Example asked for a call"):
    async with tracer.span("tool:crm.get_account", kind=kind, run_id=run_id, principal=p, level=Level.L2,
                           input=text) as sp:
        sp.add_subjects(subjects)
        sp.set_output(text)
    return sp.span_id


async def test_span_records_only_row_ids_and_readers_do_not_return_them(pg, tracer, alice):
    run_id = new_id("run")
    span_id = await _span(tracer, alice, run_id,
                          subjects=["CT-0042", "u0123", "u0123", "not an id", "u0123\n", "x" * 65, None, 7])
    (row,) = _owner("SELECT subject_ids, attrs FROM trace.spans WHERE span_id = %s", (span_id,))
    assert row["subject_ids"] == ["CT-0042", "u0123"] and "subjects_truncated" not in row["attrs"]
    (own,) = await read_run(alice, run_id)
    assert "subject_ids" not in own  # erasure metadata, not for readers


async def test_a_bare_string_is_one_id_and_too_many_are_flagged(pg, tracer, alice):
    span_id = await _span(tracer, alice, new_id("run"), subjects="CAND-007")
    assert _owner("SELECT subject_ids FROM trace.spans WHERE span_id = %s", (span_id,))[0]["subject_ids"] == \
        ["CAND-007"]
    many = [f"u{i:05d}" for i in range(MAX_SUBJECTS + 3)]
    span_id = await _span(tracer, alice, new_id("run"), subjects=many)
    (row,) = _owner("SELECT cardinality(subject_ids) AS n, attrs FROM trace.spans WHERE span_id = %s", (span_id,))
    assert row["n"] == MAX_SUBJECTS and row["attrs"]["subjects_truncated"] is True


def _write(c, span_id, run_id, subjects, status="running", ended=None):
    c.execute("SELECT trace.write_span(%s, %s, NULL, 'n', 'tool', 2::smallint, now(), %s, %s, '{}'::jsonb, NULL, NULL,"
              " %s::text[])", (span_id, run_id, ended, status, subjects))


async def test_write_span_accumulates_subjects_and_refuses_text(pg, alice):
    span_id, run_id = new_id("span"), new_id("run")
    with pgmod.principal_tx_sync(alice) as c:
        _write(c, span_id, run_id, ["u0002", "u0001"])
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            with c.transaction():
                _write(c, span_id, run_id, ["Erika Example"], status="ok", ended="2030-01-01")
        _write(c, span_id, run_id, ["u0003"], status="ok", ended="2030-01-01")  # the end write cannot drop any
    assert _owner("SELECT subject_ids FROM trace.spans WHERE span_id = %s", (span_id,))[0]["subject_ids"] == \
        ["u0001", "u0002", "u0003"]


def _thread(owner_id: str, *run_ids: str) -> str:
    thread_id = new_id("thr")
    with pgmod.connect_owner() as c:
        c.execute("INSERT INTO ops.threads (thread_id, owner_id, agent, app) VALUES (%s, %s, 'qa', 'qa')",
                  (thread_id, owner_id))
        for r in run_ids:
            c.execute("INSERT INTO ops.thread_runs (run_id, thread_id) VALUES (%s, %s)", (r, thread_id))
    return thread_id


async def test_erase_subject_blanks_every_run_of_the_threads_that_touched_the_person(pg, tracer, alice):
    subject = f"CT-{new_id('x')[-6:].upper()}"
    touched, later_turn, other_thread, alone = (new_id("run") for _ in range(4))
    _thread(alice.user_id, touched, later_turn)
    _thread(alice.user_id, other_thread)
    tool = await _span(tracer, alice, touched, subjects=[subject, "u0005"])
    llm = await _span(tracer, alice, touched, kind="llm")
    follow_up = await _span(tracer, alice, later_turn, kind="llm")  # quotes the earlier turn, no tool call
    untouched = await _span(tracer, alice, other_thread, kind="llm")
    lone = await _span(tracer, alice, alone, subjects=[subject])  # a run outside any thread

    with pgmod.principal_tx_sync(alice) as c:  # the app role may not erase (owner job)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT trace.erase_subject(%s)", (subject,))
    (n,) = _owner("SELECT trace.erase_subject(%s) AS n", (subject,))
    assert n["n"] == 4

    rows = {r["span_id"]: r for r in _owner(
        "SELECT span_id, input_text, output_text, subject_ids, attrs FROM trace.spans WHERE span_id = ANY(%s)",
        ([tool, llm, follow_up, untouched, lone],))}
    for sid in (tool, llm, follow_up, lone):
        r = rows[sid]
        assert r["input_text"] is None and r["output_text"] is None and r["attrs"].get("erased") is True, sid
        assert subject not in r["subject_ids"]
    assert rows[tool]["subject_ids"] == ["u0005"]  # other people's ids stay for their own requests
    assert rows[untouched]["input_text"] and rows[untouched]["attrs"] == {}
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _owner("SELECT trace.erase_subject(%s)", ("Erika Example",))
