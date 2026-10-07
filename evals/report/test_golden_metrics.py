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
        "je": _rows("SELECT period, account_code, dept, debit_eur, credit_eur, status FROM fin.journal_entries"),
        "inv": _rows("SELECT issued_on, due_on, paid_on, amount_eur FROM fin.invoices"),
        "bud": _rows("SELECT dept, period, budget_eur FROM fin.budgets"),
        "emp": _rows("SELECT dept, hired_on FROM hr.employees"),
        "ci": _rows("SELECT project_id, status, started_at FROM eng.ci_runs"),
        "opp": _rows("SELECT stage, amount_eur, close_date FROM crm.opportunities"),
        "req": _rows("SELECT r.dept, r.status, r.opened_on, b.approved_budget_eur, b.projected_cost_eur "
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
    eng = sum(1 for e in raw["emp"] if e["dept"] == "eng" and e["hired_on"] < AS_OF)
    assert await _served(personas, "headcount", "2026-09", {"dept": "eng"}) == eng
    assert await _served(personas, "headcount", "2026-09") == sum(1 for e in raw["emp"] if e["hired_on"] < AS_OF)
    live = [r for r in raw["req"] if r["dept"] == "eng" and r["status"] in ("open", "on_hold") and r["opened_on"] < AS_OF]
    approved = sum((r["approved_budget_eur"] for r in live), Decimal(0))
    projected = sum((r["projected_cost_eur"] for r in live), Decimal(0))
    assert await _served(personas, "hiring_budget_approved", "2026-09", {"dept": "eng"}) == approved
    assert await _served(personas, "hiring_variance_pct", "2026-09", {"dept": "eng"}) == \
        _r4((projected / approved - 1) * 100)


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
