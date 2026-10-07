"""Regression tests for review findings on the metric layer: D7 in the dept overrun signal, requisition
status in the hiring signal and the headcount summary, and the snapshot guard trigger (label of
signed rows, INSERT)."""

from __future__ import annotations

import psycopg
import pytest

from corpai.db import pg
from corpai.metrics import detect, refresh_snapshots, sign_off
from tests.metrics.conftest import owner_rows, run

SMALL_DEPTS = ("exec", "it", "legal")  # 2, 3 and 4 people in the synthetic company
_AMOUNT_KEYS = ("actual_eur", "budget_eur", "overrun_pct")


@pytest.mark.anyio
@pytest.mark.parametrize("who", ["ceo", "finance_head"])
async def test_dept_overrun_never_shows_amounts_for_depts_under_five(seeded, personas, who):
    """With threshold -100 every dept is 'over budget': a small dept must appear without any figure."""
    res = await detect(personas[who], "dept_budget_overrun", threshold_pct=-100.0)
    assert res.status == "ok"
    by = {f["ref"]: f for f in res.findings}
    for dept in SMALL_DEPTS:
        if dept in by:
            assert by[dept]["suppressed"] is True, by[dept]
            assert not any(k in by[dept] for k in _AMOUNT_KEYS), by[dept]
    big = [f for f in res.findings if not f.get("suppressed")]
    assert big and all(all(k in f for k in _AMOUNT_KEYS) for f in big)
    assert {f["ref"] for f in big}.isdisjoint(SMALL_DEPTS)


@pytest.mark.anyio
async def test_dept_overrun_default_still_reports_the_planted_sales_overrun(seeded, personas):
    res = await detect(personas["finance_head"], "dept_budget_overrun")
    sales = next(f for f in res.findings if f["ref"] == "sales")
    assert sales["actual_eur"] > sales["budget_eur"] and "suppressed" not in sales


def test_dept_size_function_returns_booleans_only_and_needs_a_principal(seeded, personas):
    with pg.principal_tx_sync(personas["sales_staff"]) as c:
        rows = c.execute("SELECT * FROM metrics.dept_below_min_group(%s)", ("2026-10-01",)).fetchall()
    assert rows and all(set(r) == {"dept", "below"} and isinstance(r["below"], bool) for r in rows)
    assert {r["dept"] for r in rows if r["below"]} >= set(SMALL_DEPTS)
    with pg.connect_app() as c:
        assert c.execute("SELECT * FROM metrics.dept_below_min_group(%s)", ("2026-10-01",)).fetchall() == []


@pytest.fixture
def filled_requisition_over_budget(seeded):
    """R-05 is filled; make its projected cost exceed the budget for the duration of one test."""
    with pg.connect_owner() as c:
        row = c.execute("SELECT r.status, b.approved_budget_eur, b.projected_cost_eur FROM hr.requisitions r "
                        "JOIN hr.requisition_budgets b USING (req_id) WHERE r.req_id = 'R-05'").fetchone()
        assert row["status"] == "filled"
        c.execute("UPDATE hr.requisition_budgets SET projected_cost_eur = approved_budget_eur * 1.5 "
                  "WHERE req_id = 'R-05'")
    try:
        yield "R-05"
    finally:
        with pg.connect_owner() as c:
            c.execute("UPDATE hr.requisition_budgets SET projected_cost_eur = %s WHERE req_id = 'R-05'",
                      (row["projected_cost_eur"],))


@pytest.mark.anyio
async def test_hiring_signal_ignores_filled_requisitions(personas, filled_requisition_over_budget):
    res = await detect(personas["hr_head"], "hiring_over_budget")
    assert filled_requisition_over_budget not in res.refs and "R-02" in res.refs
    assert all(f["status"] in ("open", "on_hold") or not f["status_visible"] for f in res.findings)


@pytest.mark.anyio
async def test_headcount_summary_marks_only_active_requisitions_over_budget(personas, filled_requisition_over_budget):
    from corpai.hr import headcount_summary

    s = await headcount_summary(personas["hr_head"])
    items = {b["req_id"]: b for b in s["budget"]["items"]}
    assert items["R-05"]["status"] == "filled" and items["R-05"]["over_budget"] is False
    assert items["R-02"]["status"] == "open" and items["R-02"]["over_budget"] is True


# ---- the snapshot guard trigger --------------------------------------------------------------

def _owner():
    return psycopg.connect(pg.owner_dsn(), autocommit=True)


def test_owner_cannot_insert_a_snapshot_as_signed_or_published(seeded):
    base = ("INSERT INTO metrics.snapshots (metric_id, period, dims, dims_key, value, unit, sql_sha256, status, "
            "signed_by, signed_at, published_by, published_at, published_level, published_acl, level, acl_groups) "
            "VALUES ('arr', '2025-11', '{}', 'forged', 1, 'EUR', repeat('0', 64), %s, %s, %s, %s, %s, 2, '{all}', "
            "%s, %s)")
    with _owner() as c:
        for params in (("signed", "u0040", "now", None, None, 3, ["dept:finance"]),
                       ("published", "u0040", "now", "u0040", "now", 2, ["all"])):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(base, params)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # a draft that claims a signature
            c.execute(base, ("draft", "u0040", "now", None, None, 3, ["dept:finance"]))


def test_signed_snapshot_label_is_frozen_until_publish(seeded, personas):
    period = "2025-11"
    refresh_snapshots(period, metric_ids=["arr"])
    run(sign_off(personas["finance_head"], period, ["arr"]))
    where = "metric_id = 'arr' AND period = %s AND status = 'signed'"
    with _owner() as c:
        for sets in ("acl_groups = '{all}'", "level = 1", "dept = 'sales'", "trust = 'untrusted'",
                     "owner_id = 'u0001'", "status = 'published', published_by = 'u0040', published_at = now()"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"UPDATE metrics.snapshots SET {sets} WHERE {where}", (period,))  # sets: test constants
        # the only legal move is metrics.publish, which sets exactly the published label
    row = owner_rows(f"SELECT acl_groups, level FROM metrics.snapshots WHERE {where}", (period,))[0]
    assert row["acl_groups"] != ["all"] and row["level"] >= 3


def test_a_draft_cannot_jump_to_published(seeded):
    period = "2025-12"
    refresh_snapshots(period, metric_ids=["customers_active"])
    with _owner() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE metrics.snapshots SET status = 'published', signed_by = 'u0040', signed_at = now(), "
                      "published_by = 'u0040', published_at = now(), level = published_level, "
                      "acl_groups = published_acl WHERE metric_id = 'customers_active' AND period = %s", (period,))
