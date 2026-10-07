"""People and hiring metrics over time (issues-log D-12, MR-12): headcount on the reporting date,
real trailing-twelve-month attrition from hr.employees.left_on, and requisition metrics that use the
status at the end of the period. Values are recomputed here from the rows (owner connection)."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from corpai.metrics import load_metrics
from corpai.metrics.refresh import compute_all
from tests.metrics.conftest import owner_rows


def _r4(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _rows(metric: str, period: str) -> dict[str, dict]:
    return {r["dims"].get("dept", "*"): r for r in compute_all(period, [metric])[metric]}


def _employees() -> list[dict]:
    return owner_rows("SELECT employee_id, dept, hired_on, left_on FROM hr.employees")


def _employed(e: dict, on: date) -> bool:
    return e["hired_on"] < on and (e["left_on"] is None or e["left_on"] >= on)


def test_attrition_reads_leaving_dates_not_account_state():
    md = load_metrics()["attrition_pct"]
    assert md.sources == ("hr.employees",) and md.min_group == 5 and md.dims == ("dept",)
    assert "core.users" not in md.sql and "active" not in md.sql


@pytest.mark.parametrize("period,ttm_start,end", [
    ("2026-09", date(2025, 10, 1), date(2026, 10, 1)),
    ("2026-01", date(2025, 2, 1), date(2026, 2, 1)),
])
def test_attrition_is_trailing_twelve_months_by_dept(seeded, period, ttm_start, end):
    rows = _rows("attrition_pct", period)
    emp = _employees()
    for dept, row in rows.items():
        group = [e for e in emp if dept == "*" or e["dept"] == dept]
        current = sum(_employed(e, end) for e in group)
        assert row["n"] == current, dept
        if current < 5:
            assert row["suppressed"] and row["value"] is None, dept
            continue
        leavers = sum(1 for e in group if e["left_on"] is not None and ttm_start <= e["left_on"] < end)
        avg2 = sum(_employed(e, ttm_start) for e in group) + current
        assert row["value"] == _r4(Decimal(leavers) * 2 / avg2 * 100), (period, dept)
    if period == "2026-09":
        assert rows["eng"]["value"] > 0 and rows["sales"]["value"] > 0
        # 4 people now (the paralegal left in May): a single leaver would point at a person
        assert rows["legal"]["suppressed"]


def test_attrition_window_moves_with_the_period(seeded):
    # u0195 (eng) left on 2025-08-31: inside the window ending 2026-01-31, outside the one ending 2026-09-30
    sep = _rows("attrition_pct", "2026-09")["eng"]["value"]
    jan = _rows("attrition_pct", "2026-01")["eng"]["value"]
    assert sep != jan


def test_a_deactivated_account_is_not_attrition(seeded):
    """MR-12: locking an account used to change attrition for every period. Now only left_on counts."""
    before = _rows("attrition_pct", "2026-09")
    uid = owner_rows("SELECT employee_id FROM hr.employees WHERE dept = 'eng' AND left_on IS NULL "
                     "AND employee_id >= 'u0100' ORDER BY employee_id LIMIT 1")[0]["employee_id"]
    from corpai.db import pg

    with pg.connect_owner() as c:
        c.execute("UPDATE core.users SET active = false WHERE user_id = %s", (uid,))
    try:
        assert _rows("attrition_pct", "2026-09") == before
    finally:
        with pg.connect_owner() as c:
            c.execute("UPDATE core.users SET active = true WHERE user_id = %s", (uid,))


def test_headcount_excludes_leavers_on_the_reporting_date(seeded):
    rows = _rows("headcount", "2026-09")
    emp = _employees()
    for dept in ("eng", "sales", "finance", "hr"):
        assert rows[dept]["value"] == sum(_employed(e, date(2026, 10, 1)) for e in emp if e["dept"] == dept)
    # u0193's last day in sales was 2026-07-31: counted on the reporting date of June (2026-07-01),
    # gone on the reporting date of July (2026-08-01)
    jun, jul = _rows("headcount", "2026-06")["sales"]["value"], _rows("headcount", "2026-07")["sales"]["value"]
    assert jun == jul + 1


def _req_status(r: dict, on: date) -> str | None:
    if r["opened_on"] >= on:
        return None
    for status, since in (("filled", r["filled_on"]), ("closed", r["closed_on"]), ("on_hold", r["on_hold_on"])):
        if since is not None and since < on:
            return status
    return "open"


@pytest.mark.parametrize("period,end", [("2026-02", date(2026, 3, 1)), ("2026-Q1", date(2026, 4, 1)),
                                        ("2026-06", date(2026, 7, 1)), ("2026-Q3", date(2026, 10, 1))])
def test_hiring_metrics_use_the_status_at_the_period_end(seeded, period, end):
    reqs = owner_rows("SELECT r.*, b.approved_budget_eur, b.projected_cost_eur FROM hr.requisitions r "
                      "JOIN hr.requisition_budgets b USING (req_id)")
    live = [r for r in reqs if _req_status(r, end) in ("open", "on_hold")]
    open_ = [r for r in reqs if _req_status(r, end) == "open"]
    approved = _rows("hiring_budget_approved", period)
    opened = _rows("open_requisitions", period)
    for dept in {r["dept"] for r in reqs}:
        want = sum((r["approved_budget_eur"] for r in live if r["dept"] == dept), Decimal(0))
        assert (approved[dept]["value"] if dept in approved else Decimal(0)) == want, (period, dept)
        want_open = sum(r["headcount"] for r in open_ if r["dept"] == dept)
        assert (opened[dept]["value"] if dept in opened else 0) == want_open, (period, dept)


def test_status_history_changes_the_answer(seeded):
    # R-05 (finance) was open in February and filled on 2026-03-20; R-04 (legal) went on hold on 2026-07-01
    assert "finance" in _rows("hiring_budget_approved", "2026-02")
    assert "finance" not in _rows("hiring_budget_approved", "2026-Q1")
    assert "legal" in _rows("open_requisitions", "2026-06")
    assert "legal" not in _rows("open_requisitions", "2026-Q3")
    assert "legal" in _rows("hiring_budget_approved", "2026-Q3")  # on hold is still being hired for


def test_cash_balance_is_positive_and_moves_with_the_flow(seeded):
    sep = compute_all("2026-09", ["cash_balance", "cash_net_flow"])
    aug = compute_all("2026-08", ["cash_balance"])
    bal_sep, flow_sep = sep["cash_balance"][0]["value"], sep["cash_net_flow"][0]["value"]
    assert bal_sep > 0
    assert bal_sep == aug["cash_balance"][0]["value"] + flow_sep


def test_the_opening_balance_is_a_balance_not_a_cash_inflow(seeded):
    """The carried-forward bank balance is posted before the window: cash_balance for 2025-09 (the
    balance on 2025-10-01) is the opening balance, and cash_net_flow for 2025-10 is October's real
    movement (a burn), not EUR 3.2M of cash received."""
    from corpai.synth.script import OPENING_BANK_BALANCE_EUR

    opening = Decimal(OPENING_BANK_BALANCE_EUR)
    assert compute_all("2025-09", ["cash_balance"])["cash_balance"][0]["value"] == opening
    oct_flow = compute_all("2025-10", ["cash_net_flow"])["cash_net_flow"][0]["value"]
    raw = owner_rows("SELECT coalesce(sum(debit_eur - credit_eur), 0) AS v FROM fin.journal_entries "
                     "WHERE account_code = '1800' AND status = 'posted' AND posted_on >= '2025-10-01' "
                     "AND posted_on < '2025-11-01'")[0]["v"]
    assert oct_flow == raw < 0  # October burned cash
    assert compute_all("2025-10", ["cash_balance"])["cash_balance"][0]["value"] == opening + oct_flow
    q4 = compute_all("2025-Q4", ["cash_net_flow"])["cash_net_flow"][0]["value"]
    assert q4 < 0 and abs(q4) < opening  # the quarter's flow is the burn, without the carried balance
