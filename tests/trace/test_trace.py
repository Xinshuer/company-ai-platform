"""Tracer and trace readers: masking by level, own-runs-only, admin metadata, dept aggregates."""

from __future__ import annotations

import sys
from datetime import timedelta

import psycopg
import pytest

from corpai import clock
from corpai.db import pg as pgmod
from corpai.ids import new_id
from corpai.trace import Tracer, default_masker, dept_stats, read_meta, read_run
from corpai.types import Dept, Level, Role

pytestmark = pytest.mark.anyio

SECRET_INPUT = "Customer Erika Example erika@nordhafen-logistik.example asked about the discount"
SECRET_OUTPUT = "Offer 23% discount to Erika Example"


def stub_masker(text: str, level: Level) -> str | None:
    return {Level.L1: text, Level.L2: text, Level.L3: "MASKED"}.get(Level(level))


@pytest.fixture
def tracer() -> Tracer:
    return Tracer(masker=stub_masker)


@pytest.fixture
def alice(mkp):
    return mkp("u0031", Role.EMPLOYEE, Dept.SALES, Level.L3)


@pytest.fixture
def bob(mkp):
    return mkp("u0032", Role.EMPLOYEE, Dept.SALES, Level.L3)


async def _one(tracer, principal, level, *, raise_to=None, kind="llm"):
    run_id = new_id("run")
    async with tracer.span("answer", kind=kind, run_id=run_id, principal=principal, level=level,
                           input=SECRET_INPUT) as sp:
        sp.set_attr("route", "local")
        if raise_to is not None:
            sp.set_level(raise_to)
        sp.set_output(SECRET_OUTPUT)
    return run_id, sp


def _owner_span(span_id: str) -> dict:
    with pgmod.connect_owner() as c:
        return c.execute("SELECT * FROM trace.spans WHERE span_id = %s", (span_id,)).fetchone()


@pytest.mark.parametrize("level,expected", [
    (Level.L1, (SECRET_INPUT, SECRET_OUTPUT)),
    (Level.L2, (SECRET_INPUT, SECRET_OUTPUT)),
    (Level.L3, ("MASKED", "MASKED")),
    (Level.L4, (None, None)),
])
async def test_text_is_masked_by_level(pg, tracer, alice, level, expected):
    run_id, sp = await _one(tracer, alice, level)
    rows = await read_run(alice, run_id)
    assert len(rows) == 1
    r = rows[0]
    assert (r["input_text"], r["output_text"]) == expected
    assert r["level"] == int(level) and r["status"] == "ok" and r["ended_at"] is not None
    assert r["user_id"] == alice.user_id and r["dept"] == "sales" and r["attrs"] == {"route": "local"}


async def test_final_level_decides_masking(pg, tracer, alice):
    """A span that starts at L2 and pulls in L4 data must store no text at all."""
    run_id, sp = await _one(tracer, alice, Level.L2, raise_to=Level.L4)
    r = _owner_span(sp.span_id)
    assert r["level"] == 4 and r["input_text"] is None and r["output_text"] is None


async def test_level_never_goes_down(pg, tracer, alice):
    run_id = new_id("run")
    async with tracer.span("n", kind="node", run_id=run_id, principal=alice, level=Level.L3,
                           input=SECRET_INPUT) as sp:
        sp.set_level(Level.L1)
        assert sp.level == Level.L3
        with pytest.raises(ValueError):
            sp.set_level(7)
    r = _owner_span(sp.span_id)
    assert r["level"] == 3 and r["input_text"] == "MASKED"


async def test_l4_text_dropped_even_with_a_leaky_masker(pg, alice):
    leaky = Tracer(masker=lambda text, level: text)  # a buggy masker that masks nothing
    run_id, sp = await _one(leaky, alice, Level.L4)
    r = _owner_span(sp.span_id)
    assert r["input_text"] is None and r["output_text"] is None


async def test_masker_failure_fails_closed(pg, alice):
    def broken(text, level):
        raise RuntimeError("classifier down")

    run_id, sp = await _one(Tracer(masker=broken), alice, Level.L1)
    r = _owner_span(sp.span_id)
    assert r["input_text"] is None and r["output_text"] is None
    assert r["attrs"]["masking_failed"] is True


async def test_default_masker_fails_closed_without_classify(pg, alice, monkeypatch):
    monkeypatch.setitem(sys.modules, "corpai.classify", None)  # import now raises ImportError
    assert default_masker("hello", Level.L1) is None
    run_id, sp = await _one(Tracer(), alice, Level.L1)
    r = _owner_span(sp.span_id)
    assert r["input_text"] is None and r["output_text"] is None


class _FailingEnd(Tracer):
    """Writes the start row, then fails the end write (connection limit, a DB check, ...)."""

    async def _finish(self, *a, **kw):
        raise psycopg.OperationalError("too many connections for role corpai_app")


async def test_failed_end_write_does_not_fail_a_successful_body(pg, alice, caplog):
    run_id = new_id("run")
    done = []
    async with _FailingEnd(masker=stub_masker).span("answer", kind="llm", run_id=run_id, principal=alice,
                                                   level=Level.L2, input="q") as sp:
        done.append(True)
    assert done == [True]  # no exception reached the caller
    assert _owner_span(sp.span_id)["status"] == "running"  # the gap stays visible
    assert any("trace write failed" in r.getMessage() for r in caplog.records)


async def test_failed_end_write_keeps_the_body_exception(pg, alice):
    with pytest.raises(KeyError):
        async with _FailingEnd(masker=stub_masker).span("answer", kind="llm", run_id=new_id("run"),
                                                       principal=alice, level=Level.L2):
            raise KeyError("x")


async def test_error_span_records_class_name_only(pg, tracer, alice):
    run_id = new_id("run")
    with pytest.raises(ValueError, match="IBAN DE89"):
        async with tracer.span("tool", kind="tool", run_id=run_id, principal=alice, level=Level.L2,
                               input="q") as sp:
            raise ValueError("bad IBAN DE89370400440532013000 for Erika Example")
    r = _owner_span(sp.span_id)
    assert r["status"] == "error" and r["ended_at"] is not None
    assert r["attrs"] == {"error_type": "ValueError"}
    flat = repr(r)
    assert "DE89" not in flat and "Erika" not in flat


async def test_requester_reads_only_own_runs(pg, tracer, alice, bob):
    run_a, _ = await _one(tracer, alice, Level.L2)
    run_b, _ = await _one(tracer, bob, Level.L2)
    assert len(await read_run(alice, run_a)) == 1
    assert await read_run(bob, run_a) == []
    assert await read_run(alice, run_b) == []
    # Even a direct query without the run filter shows only one's own spans.
    with pgmod.principal_tx_sync(bob) as c:
        users = {r["user_id"] for r in c.execute("SELECT user_id FROM trace.spans").fetchall()}
    assert users == {bob.user_id}


async def test_no_principal_or_deactivated_sees_nothing(pg, tracer, alice, mkp):
    run_a, _ = await _one(tracer, alice, Level.L2)
    with pgmod.connect_app() as c:
        assert c.execute("SELECT count(*) AS n FROM trace.spans").fetchone()["n"] == 0
    gone = mkp(alice.user_id, Role.EMPLOYEE, Dept.SALES, Level.L3, active=False)
    assert await read_run(gone, run_a) == []


async def test_platform_admin_gets_metadata_without_text(pg, tracer, alice, mkp):
    run_a, sp = await _one(tracer, alice, Level.L2)
    admin = mkp("u0067", Role.PLATFORM_ADMIN, Dept.IT, Level.L2)
    meta = await read_meta(admin, run_a)
    assert len(meta) == 1
    m = meta[0]
    assert m["span_id"] == sp.span_id and m["user_id"] == alice.user_id and m["status"] == "ok"
    assert "input_text" not in m and "output_text" not in m
    assert SECRET_INPUT not in repr(meta) and SECRET_OUTPUT not in repr(meta)
    # Admin's own read_run (RLS) shows nothing of other people's runs.
    assert await read_run(admin, run_a) == []


async def test_read_meta_denied_for_others(pg, tracer, alice, mkp):
    run_a, _ = await _one(tracer, alice, Level.L2)
    for p in (alice, mkp("u0001", Role.CEO, Dept.EXEC, Level.L4),
              mkp("u0069", Role.SECURITY_AUDITOR, Dept.IT, Level.L2),
              mkp("u0067", Role.PLATFORM_ADMIN, Dept.IT, Level.L2, active=False)):
        with pytest.raises(PermissionError):
            await read_meta(p, run_a)
    # The database refuses too, including a forged role without the group.
    forged = mkp("u0031", Role.PLATFORM_ADMIN, Dept.SALES, Level.L3, groups={"all"})
    with pgmod.principal_tx_sync(forged) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM trace.read_meta(%s)", (run_a,))


async def test_dept_stats_aggregates_without_text(pg, tracer, alice, mkp):
    since = clock.now_utc() - timedelta(seconds=1)
    await _one(tracer, alice, Level.L3)
    # migration 102: a row needs >= 5 spans from >= 5 distinct people, so add four more sales staff
    for i in range(2, 6):
        await _one(tracer, mkp(f"u091{i}", Role.EMPLOYEE, Dept.SALES, Level.L3), Level.L3)
    eng = mkp("u0040", Role.EMPLOYEE, Dept.ENG, Level.L3)
    await _one(tracer, eng, Level.L2, kind="retrieval")

    sales_head = mkp("u0030", Role.MANAGER, Dept.SALES, Level.L4)
    rows = await dept_stats(sales_head, since)
    assert rows and {r["dept"] for r in rows} == {"sales"}
    assert set(rows[0]) == {"dept", "kind", "route", "level", "status", "n"}
    assert any(r["kind"] == "llm" and r["route"] == "local" and r["level"] == 3 and r["n"] >= 1 for r in rows)
    assert SECRET_INPUT not in repr(rows) and "MASKED" not in repr(rows)



async def test_dept_stats_agrees_with_decide(pg, tracer, mkp):
    """design-review 五, trace 内容: the CEO column is 否. The CEO (head of exec) sees exec's counts
    only, exactly what decide(ceo, READ, trace of dept d) allows; nothing of sales, legal or HR."""
    from corpai.policy import decide
    from corpai.types import Action, Label, Resource

    since = clock.now_utc() - timedelta(seconds=1)
    # five people per department: migration 102 hides rows with fewer than 5 spans or 5 distinct people
    for d, dept in enumerate((Dept.SALES, Dept.LEGAL, Dept.HR, Dept.EXEC), start=2):
        for i in range(1, 6):
            await _one(tracer, mkp(f"u09{d}{i}", Role.EMPLOYEE, dept, Level.L3), Level.L3)
    ceo = mkp("u0001", Role.CEO, Dept.EXEC, Level.L4,
              groups={"all", "dept:exec", "user:u0001", "role:ceo", "head:exec"})
    sales_head = mkp("u0030", Role.MANAGER, Dept.SALES, Level.L4)
    for who in (ceo, sales_head):
        seen = {r["dept"] for r in await dept_stats(who, since)}
        allowed = {d.value for d in (Dept.SALES, Dept.LEGAL, Dept.HR, Dept.EXEC)
                   if decide(who, Action.READ, Resource("trace", "x", Label(level=Level.L3),
                                                        attrs={"owner_id": "u9999", "dept": d.value})).allow}
        assert seen == allowed == {str(who.dept)}, who.role
    # a CEO principal without head:exec (e.g. a stale session) gets nothing, in Python and in SQL
    bare_ceo = mkp("u0001", Role.CEO, Dept.EXEC, Level.L4)
    with pytest.raises(PermissionError):
        await dept_stats(bare_ceo, since)
    with pgmod.principal_tx_sync(bare_ceo) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM trace.dept_stats(%s)", (since,))


async def test_dept_stats_hides_groups_under_five_people(pg, tracer, mkp):
    """Migration 102: four people with many spans, or one person with many spans, stay hidden."""
    since = clock.now_utc() - timedelta(seconds=1)
    for i in range(1, 5):
        for _ in range(2):
            await _one(tracer, mkp(f"u098{i}", Role.EMPLOYEE, Dept.FINANCE, Level.L3), Level.L3)
    one = mkp("u0989", Role.EMPLOYEE, Dept.FINANCE, Level.L3)
    for _ in range(6):
        await _one(tracer, one, Level.L2, kind="retrieval")
    finance_head = mkp("u0040", Role.MANAGER, Dept.FINANCE, Level.L4)
    assert await dept_stats(finance_head, since) == []


async def test_dept_stats_denied_for_staff_admin_and_forged_heads(pg, mkp):
    since = clock.now_utc() - timedelta(hours=1)
    for p in (mkp("u0031", Role.EMPLOYEE, Dept.SALES, Level.L3),
              mkp("u0067", Role.PLATFORM_ADMIN, Dept.IT, Level.L2),
              mkp("u0069", Role.SECURITY_AUDITOR, Dept.IT, Level.L2)):
        with pytest.raises(PermissionError):
            await dept_stats(p, since)
    # manager role without the head:<dept> group (e.g. forged session variables) → refused by SQL
    forged = mkp("u0031", Role.MANAGER, Dept.SALES, Level.L3, groups={"all", "dept:sales"})
    with pytest.raises(PermissionError):
        await dept_stats(forged, since)


@pytest.mark.parametrize("key,value", [
    ("access_token", "x"), ("Authorization", "x"), ("headers", {"cookie": "x"}),
    ("note", "Bearer abcdefghijklmnop"), ("sessionId", "x"),
])
async def test_credential_attrs_rejected(pg, tracer, alice, key, value):
    async with tracer.span("n", kind="node", run_id=new_id("run"), principal=alice, level=Level.L2) as sp:
        with pytest.raises(ValueError):
            sp.set_attr(key, value)


async def test_attr_values_must_be_json(pg, tracer, alice):
    async with tracer.span("n", kind="node", run_id=new_id("run"), principal=alice, level=Level.L2) as sp:
        with pytest.raises(ValueError):
            sp.set_attr("obj", object())
        with pytest.raises(ValueError):
            sp.set_attr("nan", float("nan"))
        with pytest.raises(ValueError):
            sp.set_attr("blob", "x" * 10_000)
        sp.set_attr("hits", ["doc:1", "doc:2"])
        sp.set_attr("tokens", 42)
    assert _owner_span(sp.span_id)["attrs"] == {"hits": ["doc:1", "doc:2"], "tokens": 42}


async def test_bad_kind_rejected(pg, tracer, alice):
    with pytest.raises(ValueError):
        async with tracer.span("n", kind="chat", run_id=new_id("run"), principal=alice, level=Level.L1):
            pass


# ---- the write path itself ----------------------------------------------------------------------

def _write(c, span_id, run_id, level, status="running", ended=None, input_text=None, attrs="{}"):
    c.execute("SELECT trace.write_span(%s, %s, NULL, 'n', 'node', %s::smallint, now(), %s, %s, %s::jsonb, %s, NULL)",
              (span_id, run_id, level, ended, status, attrs, input_text))


async def test_app_role_cannot_write_table_directly(pg, alice):
    with pgmod.principal_tx_sync(alice) as c:
        for sql in ("INSERT INTO trace.spans (span_id, run_id, name, kind, user_id, level, started_at, status) "
                    "VALUES ('s', 'r', 'n', 'node', 'u0031', 1, now(), 'running')",
                    "UPDATE trace.spans SET input_text = 'x'",
                    "DELETE FROM trace.spans"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():
                    c.execute(sql)


async def test_write_span_needs_a_principal(pg):
    with pgmod.connect_app(autocommit=True) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _write(c, new_id("span"), new_id("run"), 1)


async def test_write_span_guards(pg, alice, bob):
    span_id, run_id = new_id("span"), new_id("run")
    with pgmod.principal_tx_sync(alice) as c:
        _write(c, span_id, run_id, 3)
    # another user cannot take over or rewrite the span
    with pgmod.principal_tx_sync(bob) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _write(c, span_id, run_id, 3, status="ok", ended="2030-01-01")
    with pgmod.principal_tx_sync(alice) as c:
        with pytest.raises(psycopg.errors.InvalidParameterValue):  # level may not go down
            with c.transaction():
                _write(c, span_id, run_id, 2, status="ok", ended="2030-01-01", input_text="raw")
        with pytest.raises(psycopg.errors.CheckViolation):  # L4 keeps no text
            with c.transaction():
                _write(c, span_id, run_id, 4, status="ok", ended="2030-01-01", input_text="raw")
        with pytest.raises(psycopg.errors.InvalidParameterValue):  # credential keys refused in SQL too
            with c.transaction():
                _write(c, span_id, run_id, 3, status="ok", ended="2030-01-01", attrs='{"x": {"Api-Key": 1}}')
        _write(c, span_id, run_id, 3, status="ok", ended="2030-01-01", input_text="MASKED")
    with pgmod.principal_tx_sync(alice) as c:  # an ended span is frozen
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _write(c, span_id, run_id, 3, status="ok", ended="2030-01-01", input_text="changed")
    assert _owner_span(span_id)["input_text"] == "MASKED"


async def test_span_survives_rolled_back_business_transaction(pg, tracer, alice):
    run_id = new_id("run")

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with pgmod.principal_tx(alice, run_id=run_id):
            async with tracer.span("n", kind="node", run_id=run_id, principal=alice, level=Level.L1, input="hi"):
                pass
            raise Boom()
    rows = await read_run(alice, run_id)
    assert len(rows) == 1 and rows[0]["input_text"] == "hi"


async def test_default_masker_with_real_classify(pg, alice):
    """Integration with stream A2: secrets never stored, L4 never stored."""
    pytest.importorskip("corpai.classify")
    key = "sk-" + "a1b2c3d4" * 4
    assert default_masker(f"use {key}", Level.L1) is not None
    assert key not in default_masker(f"use {key}", Level.L1)
    assert default_masker("anything", Level.L4) is None
    run_id = new_id("run")
    async with Tracer().span("n", kind="llm", run_id=run_id, principal=alice, level=Level.L2,
                             input=f"call with {key}") as sp:
        sp.set_output("ok")
    r = _owner_span(sp.span_id)
    assert r["input_text"] is not None and key not in r["input_text"]
