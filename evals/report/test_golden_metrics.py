"""Golden values: a handful of metrics recomputed in plain Python from the raw synth rows (owner
connection, no SQL aggregates) must equal what the metric layer serves. Financial numbers must match
exactly (design-review 二.13: "财务要求数字和 SQL 结果完全一致")."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest

from corpai.db import pg
from corpai.metrics import get_metric

pytestmark = [pytest.mark.eval, pytest.mark.anyio]

AS_OF = date(2026, 10, 1)
TTM_START = date(2025, 10, 1)
Q3 = ("2026-07", "2026-08", "2026-09")


def _rows(sql: str) -> list[dict]:
    with pg.connect_owner() as c:
        return c.execute(sql).fetchall()


def _r4(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@pytest.fixture(scope="module")
def raw(seeded):
    return {
        "coa": {r["account_code"]: r["type"] for r in _rows("SELECT account_code, type FROM fin.chart_of_accounts")},
        "je": _rows("SELECT period, posted_on, account_code, dept, debit_eur, credit_eur, status "
                    "FROM fin.journal_entries"),
        "inv": _rows("SELECT issued_on, due_on, paid_on, amount_eur FROM fin.invoices"),
        "bud": _rows("SELECT dept, period, budget_eur FROM fin.budgets"),
        "emp": _rows("SELECT dept, hired_on, left_on FROM hr.employees"),
        "ci": _rows("SELECT project_id, status, started_at FROM eng.ci_runs"),
        "opp": _rows("SELECT stage, amount_eur, close_date FROM crm.opportunities"),
        "req": _rows("SELECT r.req_id, r.dept, r.headcount, r.status, r.opened_on, r.on_hold_on, r.filled_on, "
                     "r.closed_on, b.approved_budget_eur, b.projected_cost_eur "
                     "FROM hr.requisitions r JOIN hr.requisition_budgets b USING (req_id)"),
    }


def _revenue(raw, periods) -> Decimal:
    total = Decimal(0)
    for r in raw["je"]:
        if r["status"] == "posted" and r["period"] in periods and raw["coa"][r["account_code"]] == "revenue":
            total += r["credit_eur"] - r["debit_eur"]
    return total


def _opex(raw, periods) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(Decimal)
    for r in raw["je"]:
        if r["status"] == "posted" and r["period"] in periods and raw["coa"][r["account_code"]] == "expense":
            out[r["dept"]] += r["debit_eur"] - r["credit_eur"]
    return out


def _employed(e, on: date) -> bool:
    """At the start of day `on`: hired before it, last working day (left_on) not before it."""
    return e["hired_on"] < on and (e["left_on"] is None or e["left_on"] >= on)


def _req_status(r, on: date) -> str | None:
    """A requisition's status at the start of day `on`, from its status dates (D-12)."""
    if r["opened_on"] >= on:
        return None
    for status, since in (("filled", r["filled_on"]), ("closed", r["closed_on"]), ("on_hold", r["on_hold_on"])):
        if since is not None and since < on:
            return status
    return "open"


async def _served(personas, metric_id, period, dims=None):
    mv = await get_metric(personas["ceo"], metric_id, period, dims)
    assert mv.available and not mv.suppressed, (metric_id, period, dims, mv.status)
    return mv.value


async def test_revenue(refreshed, personas, raw):
    assert await _served(personas, "revenue_recognised", "2026-09") == _revenue(raw, ("2026-09",))
    assert await _served(personas, "revenue_recognised", "2026-Q3") == _revenue(raw, Q3)


async def test_overdue_receivables(refreshed, personas, raw, planted):
    unpaid = [i for i in raw["inv"] if i["issued_on"] < AS_OF and (i["paid_on"] is None or i["paid_on"] >= AS_OF)]
    overdue = sum((i["amount_eur"] for i in unpaid if i["due_on"] < AS_OF), Decimal(0))
    over60 = sum((i["amount_eur"] for i in unpaid if (AS_OF - i["due_on"]).days > 60), Decimal(0))
    assert await _served(personas, "ar_overdue_total", "2026-09") == overdue
    assert await _served(personas, "ar_overdue_60d", "2026-09") == over60
    assert float(over60) == planted["overdue_receivable"]["amount_over_60_days_eur"]
    revenue = _revenue(raw, ("2026-09",))
    open_ar = sum((i["amount_eur"] for i in unpaid), Decimal(0))
    assert await _served(personas, "dso_days", "2026-09") == _r4(open_ar / revenue * 30)


async def test_opex_vs_budget(refreshed, personas, raw, planted):
    act = _opex(raw, Q3)
    bud: dict[str, Decimal] = defaultdict(Decimal)
    for b in raw["bud"]:
        if b["period"] in Q3:
            bud[b["dept"]] += b["budget_eur"]
    for dept in ("sales", "eng", "finance", "hr"):
        assert await _served(personas, "opex_actual", "2026-Q3", {"dept": dept}) == act[dept]
        assert await _served(personas, "opex_budget", "2026-Q3", {"dept": dept}) == bud[dept]
        pct = _r4((act[dept] / bud[dept] - 1) * 100)
        assert await _served(personas, "opex_variance_pct", "2026-Q3", {"dept": dept}) == pct
    exp = planted["dept_budget_overrun"]
    sales = await _served(personas, "opex_variance_pct", "2026-Q3", {"dept": "sales"})
    assert round(float(sales), 2) == exp["overrun_pct"] and float(act["sales"]) == exp["actual_eur"]


async def test_headcount_and_hiring(refreshed, personas, raw):
    eng = sum(1 for e in raw["emp"] if e["dept"] == "eng" and _employed(e, AS_OF))
    assert await _served(personas, "headcount", "2026-09", {"dept": "eng"}) == eng
    assert await _served(personas, "headcount", "2026-09") == sum(1 for e in raw["emp"] if _employed(e, AS_OF))
    assert eng < sum(1 for e in raw["emp"] if e["dept"] == "eng" and e["hired_on"] < AS_OF), "leavers are excluded"
    live = [r for r in raw["req"] if r["dept"] == "eng" and _req_status(r, AS_OF) in ("open", "on_hold")]
    approved = sum((r["approved_budget_eur"] for r in live), Decimal(0))
    projected = sum((r["projected_cost_eur"] for r in live), Decimal(0))
    assert await _served(personas, "hiring_budget_approved", "2026-09", {"dept": "eng"}) == approved
    variance = await _served(personas, "hiring_variance_pct", "2026-09", {"dept": "eng"})
    assert variance == _r4((projected / approved - 1) * 100)


async def test_attrition_is_trailing_twelve_months(refreshed, personas, raw):
    """Leavers with a last working day in [2025-10-01, 2026-10-01) over the average of the headcount
    at both ends, by dept; small departments stay hidden (D-12, D7)."""
    for dept in ("eng", "sales"):
        emp = [e for e in raw["emp"] if e["dept"] == dept]
        leavers = sum(1 for e in emp if e["left_on"] is not None and TTM_START <= e["left_on"] < AS_OF)
        start = sum(1 for e in emp if _employed(e, TTM_START))
        end = sum(1 for e in emp if _employed(e, AS_OF))
        assert leavers > 0, dept
        expected = _r4(Decimal(leavers) * 2 / (start + end) * 100)
        assert await _served(personas, "attrition_pct", "2026-09", {"dept": dept}) == expected
    legal = await get_metric(personas["ceo"], "attrition_pct", "2026-09", {"dept": "legal"})
    assert legal.suppressed and legal.value is None  # 4 people: one leaver would point at a person


def _computed(metric_id: str, period: str) -> dict[str, Decimal]:
    # read-only: earlier periods are recomputed without writing snapshots (tests/metrics owns them)
    from corpai.metrics.refresh import compute_all

    return {r["dims"].get("dept", "*"): r["value"] for r in compute_all(period, [metric_id])[metric_id]}


async def test_hiring_metrics_use_the_status_at_the_period_end(refreshed, personas, raw):
    """R-05 (finance) was filled on 2026-03-20: it counts in February and no longer at the end of Q1.
    R-04 (legal) went on hold on 2026-07-01: an open position in June, not at the end of Q3."""
    fin = [r for r in raw["req"] if r["dept"] == "finance" and _req_status(r, date(2026, 1, 13)) == "open"]
    assert [r["req_id"] for r in fin] == ["R-05"] and _req_status(fin[0], date(2026, 4, 1)) == "filled"
    assert _computed("hiring_budget_approved", "2026-02")["finance"] == fin[0]["approved_budget_eur"]
    assert _computed("open_requisitions", "2026-02")["finance"] == fin[0]["headcount"]
    assert "finance" not in _computed("hiring_budget_approved", "2026-Q1")
    assert "finance" not in _computed("open_requisitions", "2026-Q1")
    june = [r for r in raw["req"] if _req_status(r, date(2026, 7, 1)) == "open"]
    assert "R-04" in {r["req_id"] for r in june}
    assert _computed("open_requisitions", "2026-06")["*"] == sum(r["headcount"] for r in june)
    # what the metric layer serves for the current quarter (signed-off path, snapshots)
    assert await _served(personas, "open_requisitions", "2026-Q3") == sum(
        r["headcount"] for r in raw["req"] if _req_status(r, AS_OF) == "open")
    assert await _served(personas, "hiring_budget_approved", "2026-Q3", {"dept": "legal"}) == sum(
        r["approved_budget_eur"] for r in raw["req"] if r["req_id"] == "R-04")  # on hold: still being hired for


async def test_cash_balance_includes_the_opening_balance_and_series_b(refreshed, personas, raw):
    bank = [r for r in raw["je"] if r["account_code"] == "1800" and r["status"] == "posted"]
    balance = sum((r["debit_eur"] - r["credit_eur"] for r in bank if r["posted_on"] < AS_OF), Decimal(0))
    flow = sum((r["debit_eur"] - r["credit_eur"] for r in bank if r["period"] == "2026-09"), Decimal(0))
    assert await _served(personas, "cash_balance", "2026-09") == balance
    assert await _served(personas, "cash_net_flow", "2026-09") == flow
    assert balance > 0 > flow  # funded, and burning cash


async def test_ci_pass_rate_and_win_rate(refreshed, personas, raw):
    runs = [r for r in raw["ci"] if r["project_id"] == "P-02"
            and date(2026, 7, 1) <= r["started_at"].astimezone(timezone.utc).date() < AS_OF]
    expected = _r4(Decimal(sum(r["status"] == "passed" for r in runs)) / len(runs) * 100)
    assert await _served(personas, "ci_pass_rate_pct", "2026-Q3", {"project_id": "P-02"}) == expected
    closed = [o for o in raw["opp"] if o["stage"] in ("closed_won", "closed_lost")
              and date(2025, 10, 1) <= o["close_date"] < AS_OF]
    won = sum(o["stage"] == "closed_won" for o in closed)
    assert await _served(personas, "win_rate_pct", "2026-09") == _r4(Decimal(won) / len(closed) * 100)


async def test_legal_counts_match_the_planted_answers(refreshed, personas, planted):
    expiring = planted["contract_expiring"]["signed_contracts_expiring_2026_11"]
    assert await _served(personas, "contracts_expiring_next_month", "2026-09") == len(expiring)
    gaps = planted["missing_dpa_clause"]["contracts_with_gap"]
    assert await _served(personas, "contracts_missing_dpa", "2026-09") == len(gaps)
