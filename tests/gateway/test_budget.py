"""Per-department, per-day token budgets (rule 7) and the SQL functions behind them.

The day is the wall-clock UTC date chosen by the database (ops.budget_day()); the sim date never
advances, so a budget keyed on it would never reset. Refunds go through reservation ids."""

from __future__ import annotations

import psycopg
import pytest

from corpai.db import pg as pgmod
from corpai.gateway import BudgetExceeded, ChatRequest, ops
from corpai.types import Dept, Level

pytestmark = [pytest.mark.anyio, pytest.mark.security]


def req(principal, *, run_id, hwm=Level.L1) -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": "What is deferred revenue?"}], principal=principal,
                       hwm=hwm, purpose="qa.answer", run_id=run_id, agent="qa", max_tokens=100)


def _budget(dept: str) -> dict | None:
    with pgmod.connect_owner() as c:
        return c.execute("SELECT * FROM ops.budgets WHERE dept = %s AND day = %s", (dept, ops.budget_day())).fetchone()


def _set_limit(dept: str, *, headroom: int) -> None:
    """Make today's limit for `dept` equal to what is used plus `headroom`."""
    with pgmod.connect_owner() as c:
        c.execute("""INSERT INTO ops.budgets (dept, day, tokens_used, token_limit) VALUES (%s, %s, 0, %s)
                     ON CONFLICT (dept, day) DO UPDATE SET token_limit = ops.budgets.tokens_used + %s""",
                  (dept, ops.budget_day(), headroom, headroom))


@pytest.fixture
def legal_staff(mkp):
    p = mkp("u0021", dept=Dept.LEGAL, clearance=Level.L4)
    yield p
    with pgmod.connect_owner() as c:  # restore the default so other tests are unaffected
        c.execute("UPDATE ops.budgets SET token_limit = 2000000 WHERE dept = 'legal' AND day = %s", (ops.budget_day(),))


async def test_usage_is_settled_to_actual_tokens(gw, legal_staff, run_id):
    await gw.chat(req(legal_staff, run_id=run_id))  # creates today's row
    before = _budget("legal")["tokens_used"]
    res = await gw.chat(req(legal_staff, run_id=run_id))
    after = _budget("legal")["tokens_used"]
    # the reservation (prompt estimate + max_tokens) was replaced by the reported usage
    assert after - before == res.usage["total_tokens"]


async def test_budget_exhausted(gw, fake, legal_staff, run_id, audit_rows, egress_rows):
    await gw.chat(req(legal_staff, run_id=run_id + "-warm"))
    _set_limit("legal", headroom=50)  # less than the 100 max_tokens reservation
    used = _budget("legal")["tokens_used"]
    n_calls = len(fake.calls)
    with pytest.raises(BudgetExceeded):
        await gw.chat(req(legal_staff, run_id=run_id))
    assert len(fake.calls) == n_calls  # the backend was never called
    assert egress_rows(run_id) == []  # nor was anything logged as sent
    assert _budget("legal")["tokens_used"] == used  # nothing consumed by the refused call
    (row,) = audit_rows(run_id)
    assert (row["event"], row["decision"], row["reason"]) == ("budget_exceeded", "deny", "budget_exceeded")
    assert row["details"]["dept"] == "legal"


async def test_budget_is_per_department(gw, legal_staff, mkp, run_id):
    _set_limit("legal", headroom=0)
    with pytest.raises(BudgetExceeded):
        await gw.chat(req(legal_staff, run_id=run_id))
    res = await gw.chat(req(mkp("u0050", dept=Dept.ENG), run_id=run_id))  # eng is unaffected
    assert res.text


async def test_failed_backend_call_is_refunded(make_gw, fake, legal_staff, run_id):
    from corpai.gateway import BackendError, RetryConfig

    gw = make_gw(retry=RetryConfig(max_attempts=1, base_delay_s=0.01, max_delay_s=0.01))
    await gw.chat(req(legal_staff, run_id=run_id))
    used = _budget("legal")["tokens_used"]

    def boom(messages, *, route, json_mode):
        raise RuntimeError("backend exploded")

    fake.register("qa.", boom)
    with pytest.raises(BackendError):
        await gw.chat(req(legal_staff, run_id=run_id))
    assert _budget("legal")["tokens_used"] == used


def test_sql_consume_needs_matching_department(pg, mkp):
    sales = mkp("u0031", dept=Dept.SALES)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(sales) as conn:
            conn.execute("SELECT * FROM ops.consume_budget('legal', 1)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.connect_app() as conn:  # no principal
            conn.execute("SELECT * FROM ops.consume_budget('sales', 1)")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        with pgmod.principal_tx_sync(sales) as conn:
            conn.execute("SELECT * FROM ops.consume_budget('sales', -5)")


def test_budget_day_is_todays_utc_date_not_the_sim_date(pg, mkp):
    """The sim date is fixed (2026-10-01): keyed on it the daily budget would never reset."""
    from corpai import clock

    with pgmod.connect_app() as conn:
        assert conn.execute("SELECT ops.budget_day() AS d").fetchone()["d"] == ops.budget_day()
    assert ops.budget_day() == clock.now_utc().date()
    # the caller cannot pick a day: there is no day parameter to open a fresh budget with
    with pytest.raises(psycopg.errors.UndefinedFunction):
        with pgmod.principal_tx_sync(mkp("u0031", dept=Dept.SALES)) as conn:
            conn.execute("SELECT * FROM ops.consume_budget('sales', '2099-01-01'::date, 1)")


def _set_today(dept: str, used: int, limit: int) -> None:
    with pgmod.connect_owner() as c:
        c.execute("""INSERT INTO ops.budgets (dept, day, tokens_used, token_limit) VALUES (%s, ops.budget_day(), %s, %s)
                     ON CONFLICT (dept, day) DO UPDATE SET tokens_used = %s, token_limit = %s""",
                  (dept, used, limit, used, limit))


@pytest.fixture
def hr_budget():
    yield
    with pgmod.connect_owner() as c:
        c.execute("UPDATE ops.budgets SET tokens_used = 0, token_limit = 2000000 "
                  "WHERE dept = 'hr' AND day = ops.budget_day()")


def test_sql_consume_returns_negative_and_consumes_nothing(pg, mkp, hr_budget):
    hr = mkp("u0060", dept=Dept.HR)
    _set_today("hr", 90, 100)
    with pgmod.principal_tx_sync(hr) as conn:
        first = conn.execute("SELECT * FROM ops.consume_budget('hr', 10)").fetchone()
        assert first["remaining"] == 0 and first["reservation_id"] is not None
        refused = conn.execute("SELECT * FROM ops.consume_budget('hr', 5)").fetchone()
        assert refused["remaining"] == -5 and refused["reservation_id"] is None
        # settle refunds what this reservation took, never below zero, never more
        assert conn.execute("SELECT ops.settle_budget(%s, 0) AS r", (first["reservation_id"],)).fetchone()["r"] == 10
    with pgmod.connect_owner() as c:
        assert c.execute("SELECT tokens_used FROM ops.budgets WHERE dept='hr' AND day=ops.budget_day()").fetchone()[
            "tokens_used"] == 90


def test_settle_cannot_reset_the_budget_with_a_made_up_amount(pg, mkp, hr_budget):
    """The old settle_budget(dept, day, reserved, actual) trusted `reserved`: settle(10**12, 0) set
    usage to 0. Now a refund is bound to one real reservation of the same user, once."""
    hr, other = mkp("u0060", dept=Dept.HR), mkp("u0061", dept=Dept.HR)
    _set_today("hr", 1000, 2000)
    with pgmod.principal_tx_sync(hr) as conn:
        rid = conn.execute("SELECT * FROM ops.consume_budget('hr', 10)").fetchone()["reservation_id"]
    for bad_rid in (rid + 10**6, -1):  # no such reservation
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with pgmod.principal_tx_sync(hr) as conn:
                conn.execute("SELECT ops.settle_budget(%s, 0)", (bad_rid,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # someone else's reservation
        with pgmod.principal_tx_sync(other) as conn:
            conn.execute("SELECT ops.settle_budget(%s, 0)", (rid,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # no principal at all
        with pgmod.connect_app() as conn:
            conn.execute("SELECT ops.settle_budget(%s, 0)", (rid,))
    with pgmod.principal_tx_sync(hr) as conn:
        assert conn.execute("SELECT ops.settle_budget(%s, 0) AS r", (rid,)).fetchone()["r"] == 1000
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):  # only once
        with pgmod.principal_tx_sync(hr) as conn:
            conn.execute("SELECT ops.settle_budget(%s, 0)", (rid,))
    with pgmod.connect_owner() as c:
        assert c.execute("SELECT tokens_used FROM ops.budgets WHERE dept='hr' AND day=ops.budget_day()").fetchone()[
            "tokens_used"] == 1000  # what was used before is still counted


def test_old_trusting_settle_signature_is_gone(pg, mkp):
    with pytest.raises(psycopg.errors.UndefinedFunction):
        with pgmod.principal_tx_sync(mkp("u0060", dept=Dept.HR)) as conn:
            conn.execute("SELECT ops.settle_budget('hr', ops.budget_day(), 1000000000000::bigint, 0::bigint)")


async def test_gateway_settles_its_own_reservation(gw, legal_staff, run_id):
    await gw.chat(req(legal_staff, run_id=run_id))
    with pgmod.connect_owner() as c:
        rows = c.execute("SELECT * FROM ops.budget_reservations WHERE run_id = %s", (run_id,)).fetchall()
    assert len(rows) == 1 and rows[0]["settled_at"] is not None and rows[0]["user_id"] == legal_staff.user_id


def test_app_role_cannot_read_or_write_budget_tables(pg, mkp):
    ceo = mkp("u0001", dept=Dept.EXEC, clearance=Level.L4)
    for sql in ("SELECT * FROM ops.budgets", "SELECT * FROM ops.budget_defaults",
                "UPDATE ops.budgets SET tokens_used = 0", "SELECT * FROM ops.budget_reservations",
                "UPDATE ops.budget_reservations SET tokens = 0"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with pgmod.principal_tx_sync(ceo) as conn:
                conn.execute(sql)
