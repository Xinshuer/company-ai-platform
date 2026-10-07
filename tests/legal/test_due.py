"""contracts_due: exact SQL answer for the planted expiring contract, relative to the simulation date."""

from __future__ import annotations

from datetime import date

import pytest

from corpai import clock
from corpai.legal import contracts_due, month_bounds

pytestmark = pytest.mark.anyio


async def test_planted_c0003_for_november(personas, fake_llm):
    assert clock.today() == date(2026, 10, 1)
    for who in ("legal_staff", "legal_head", "ceo"):
        rows = await contracts_due(personas[who], month="2026-11")
        assert [r["contract_id"] for r in rows] == ["C-0003"], who
        r = rows[0]
        assert r["expires_on"] == date(2026, 11, 30) and r["notice_days"] == 45
        assert r["notice_deadline"] == date(2026, 10, 16)
        assert r["auto_renew"] is True and r["days_to_notice_deadline"] == 15
        assert r["notice_deadline_passed"] is False and "2026-10-16" in r["action"]
    assert fake_llm.calls == []  # SQL, not a model


async def test_default_month_is_next_month_of_the_simulation_date(personas):
    assert month_bounds(None) == (date(2026, 11, 1), date(2026, 12, 1))
    rows = await contracts_due(personas["legal_staff"])
    assert [r["contract_id"] for r in rows] == ["C-0003"]


async def test_rls_decides_who_sees_due_contracts(personas, owner_query):
    for who in ("sales_staff", "eng_head", "platform_admin", "auditor"):
        assert await contracts_due(personas[who], month="2026-11") == [], who
    # the head of legal sees exactly what the table holds for a month, the handler only their own
    expected = {r["contract_id"] for r in owner_query(
        "SELECT contract_id FROM legal.contracts WHERE status = 'signed' AND expires_on >= '2027-06-01' "
        "AND expires_on < '2027-07-01'")}
    got = {r["contract_id"] for r in await contracts_due(personas["legal_head"], month="2027-06")}
    assert got == expected and "C-0005" in got
    staff = await contracts_due(personas["legal_staff"], month="2027-06")
    assert all(r["handler_id"] == "u0031" for r in staff)


@pytest.mark.parametrize("bad", ["2026-13", "2026-11\n", "2026-1", "11/2026", "2026-11-01", "", "1999-01", 202611])
async def test_bad_month_is_rejected(personas, bad):
    with pytest.raises(ValueError):
        await contracts_due(personas["legal_head"], month=bad)


def test_month_bounds_wrap_the_year():
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))
