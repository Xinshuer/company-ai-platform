"""Synth realism for period metrics (issues-log D-12): leavers with a last working day, requisition
status dates, an opening bank balance plus the Series B receipt, and no closed deal in the future.
Checked in memory for both sizes (pure generation) and in the database for the small company."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import pytest

from corpai import synth
from corpai.db import pg as pgmod
from corpai.synth._util import END, START, money, month_end, months, period
from corpai.synth.script import LEAVERS, OPENING_BANK_BALANCE_EUR, REQUISITIONS, SERIES_B

SIM_TODAY = date(2026, 10, 1)
TTM_START = date(2025, 10, 1)


@pytest.fixture(scope="module", params=[True, False], ids=["small", "full"])
def world(request):
    return synth.generate(7, small=request.param)


def test_leavers_have_records_and_deactivated_accounts(world):
    emp = {e["employee_id"]: e for e in world.tables["hr.employees"]}
    users = {u["user_id"]: u for u in world.tables["core.users"]}
    left = {k: e["left_on"] for k, e in emp.items() if e["left_on"] is not None}
    assert left == {lv.user_id: lv.left_on for lv in LEAVERS}
    for uid in left:
        assert users[uid]["active"] is False and users[uid]["persona_key"] is None
        assert emp[uid]["hired_on"] <= left[uid]
    # everybody else is a current employee with an active account
    assert all(users[u]["active"] for u in emp if u not in left)
    assert not {m[0] for m in world.memberships} & set(left), "former employees hold no memberships"


def test_leavers_in_the_window_include_departments_of_five_or_more(world):
    emp = world.tables["hr.employees"]
    current = defaultdict(int)
    for e in emp:
        if e["left_on"] is None:
            current[e["dept"]] += 1
    in_window = [e for e in emp if e["left_on"] is not None and TTM_START <= e["left_on"] < SIM_TODAY]
    assert len(in_window) >= 4
    big = {e["dept"] for e in in_window if current[e["dept"]] >= 5}
    assert {"eng", "sales"} <= big
    assert any(current[e["dept"]] < 5 for e in in_window), "a small dept leaver checks the suppression"
    assert any(e["left_on"] is not None and e["left_on"] < TTM_START for e in emp), "one leaver outside the window"


def test_payroll_stops_after_the_last_working_day(world):
    """Ledger payroll per dept and month equals the pay of the people employed through that month."""
    emp = {e["employee_id"]: e for e in world.tables["hr.employees"]}
    expected: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for c in world.tables["hr.compensation"]:
        e = emp[c["employee_id"]]
        for m in months():
            end = month_end(m)
            if e["hired_on"] <= end and (e["left_on"] is None or e["left_on"] >= end):
                expected[(e["dept"], period(m))] += c["base_salary_eur"] / 12
    booked: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for r in world.tables["fin.journal_entries"]:
        if r["account_code"] == "6000":
            booked[(r["dept"], r["period"])] += r["debit_eur"]
    assert set(booked) == set(expected)
    for k, v in expected.items():
        assert booked[k] == money(v), k
    # a leaver's month after leaving really is cheaper (u0193 left sales on 2026-07-31)
    assert expected[("sales", "2026-08")] < expected[("sales", "2026-07")]


def test_requisition_status_dates(world):
    rows = {r["req_id"]: r for r in world.tables["hr.requisitions"]}
    for spec in REQUISITIONS:
        r = rows[spec.req_id]
        for status, col in (("on_hold", "on_hold_on"), ("filled", "filled_on"), ("closed", "closed_on")):
            assert (r["status"] == status) == (r[col] is not None), (spec.req_id, status)
            if r[col] is not None:
                assert r["opened_on"] <= r[col] < SIM_TODAY
        assert spec.status_on(SIM_TODAY) == r["status"]
    # history: R-05 was open in Q1 and filled since March; R-04 was open until July
    r5, r4 = (next(s for s in REQUISITIONS if s.req_id == k) for k in ("R-05", "R-04"))
    assert r5.status_on(date(2026, 2, 1)) == "open" and r5.status_on(date(2026, 4, 1)) == "filled"
    assert r4.status_on(date(2026, 7, 1)) == "open" and r4.status_on(date(2026, 8, 1)) == "on_hold"
    assert r4.status_on(date(2026, 4, 13)) is None  # not opened yet at the start of its opening day


def test_bank_has_an_opening_balance_and_the_series_b(world):
    bank = sorted((r for r in world.tables["fin.journal_entries"] if r["account_code"] == "1800"),
                  key=lambda r: r["posted_on"])
    first = [r for r in bank if r["debit_eur"] == Decimal(OPENING_BANK_BALANCE_EUR)]
    assert len(first) == 1 and "Opening balance" in first[0]["description"]
    # before the window: in every balance, in no period's flow (cash_net_flow for 2025-10 is the real burn)
    assert first[0]["posted_on"] == START - timedelta(days=1) and first[0]["period"] == "2025-09"
    assert bank[0] is first[0]
    on, amount, investor = SERIES_B
    funding = [r for r in bank if r["posted_on"] == on and r["debit_eur"] == Decimal(amount)]
    assert len(funding) == 1 and investor in funding[0]["description"]
    # every transaction balances, so equity carries the other side of both
    equity = sum((r["credit_eur"] - r["debit_eur"] for r in world.tables["fin.journal_entries"]
                  if r["account_code"] == "2000"), Decimal(0))
    assert equity == Decimal(OPENING_BANK_BALANCE_EUR + amount)
    # the balance never goes negative at a month end
    for m in months():
        end = month_end(m)
        bal = sum((r["debit_eur"] - r["credit_eur"] for r in bank if r["status"] == "posted" and r["posted_on"] <= end),
                  Decimal(0))
        assert bal > 0, end


def test_closed_deals_never_close_after_the_window(world):
    for o in world.tables["crm.opportunities"]:
        if o["stage"] in ("closed_won", "closed_lost"):
            assert o["close_date"] <= END, o["opp_id"]
        else:
            assert o["close_date"] > END, o["opp_id"]  # open deals have an expected close date ahead


def test_written_to_the_database(synth_small):
    with pgmod.connect_owner() as c:
        n = c.execute("SELECT count(*) AS n FROM hr.employees WHERE left_on IS NOT NULL").fetchone()["n"]
        inactive = c.execute("SELECT count(*) AS n FROM core.users WHERE NOT active").fetchone()["n"]
        dates = c.execute("SELECT req_id, on_hold_on, filled_on FROM hr.requisitions "
                          "WHERE req_id IN ('R-04', 'R-05') ORDER BY req_id").fetchall()
    assert n == inactive == len(LEAVERS)
    assert dates[0]["on_hold_on"] == date(2026, 7, 1) and dates[1]["filled_on"] == date(2026, 3, 20)
