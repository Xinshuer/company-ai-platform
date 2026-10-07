"""Headcount summary: roles/headcount (L2) apart from budgets (L3); staff counts with n<5 suppression (D7)."""

from __future__ import annotations

import pytest

from corpai.db import pg
from corpai.hr import NoPermission, headcount_summary
from tests.hr.conftest import owner_rows

pytestmark = pytest.mark.anyio


def _true_counts() -> dict[str, int]:
    return {r["dept"]: r["n"] for r in owner_rows("SELECT dept, count(*)::int AS n FROM hr.employees GROUP BY dept")}


def _reqs(summary) -> set[str]:
    return {r["req_id"] for d in summary["public"]["requisitions_by_dept"] for r in d["roles"]}


async def test_hr_head_sees_everything_with_small_groups_suppressed(personas):
    s = await headcount_summary(personas["hr_head"])
    assert s["public"]["level"] == 2 and s["budget"]["level"] == 3 and s["level"] == 3
    assert _reqs(s) == {f"R-0{i}" for i in range(1, 7)}
    eng = next(d for d in s["public"]["requisitions_by_dept"] if d["dept"] == "eng")
    assert eng["open_headcount"] == 4  # R-01 (2) + R-02 + R-06, all open
    truth = _true_counts()
    staff = {x["dept"]: x for x in s["public"]["staff_by_dept"]}
    assert set(staff) == set(truth)
    for dept, n in truth.items():
        if n < 5:
            assert staff[dept]["employees"] is None and staff[dept]["suppressed"] is True, dept
        else:
            assert staff[dept]["employees"] == n and staff[dept]["suppressed"] is False, dept
    assert any(x["suppressed"] for x in staff.values())  # exec (2), legal (4), it (3) are small
    budget = {b["req_id"]: b for b in s["budget"]["items"]}
    assert set(budget) == _reqs(s) and s["budget"]["denied"] == []
    assert budget["R-02"]["over_budget"] is True and budget["R-02"]["variance_pct"] == pytest.approx(13.68, 0.01)


async def test_department_head_sees_own_department_only(personas):
    s = await headcount_summary(personas["sales_head"])
    assert _reqs(s) == {"R-03"}
    assert [x["dept"] for x in s["public"]["staff_by_dept"]] == ["sales"]
    assert s["public"]["staff_by_dept"][0]["employees"] == _true_counts()["sales"]
    denied = {x["dept"] for x in s["public"]["staff_denied"]}
    assert "eng" in denied and "sales" not in denied
    assert all(x["reason"] == "no permission" for x in s["public"]["staff_denied"])
    assert [b["req_id"] for b in s["budget"]["items"]] == ["R-03"]


async def test_cfo_sees_budgets_of_every_requisition_but_only_finance_staff(personas):
    s = await headcount_summary(personas["finance_head"])
    assert _reqs(s) == {"R-05"}  # requisition rows: head of the hiring dept only
    budgets = {b["req_id"]: b for b in s["budget"]["items"]}
    assert set(budgets) == {f"R-0{i}" for i in range(1, 7)}  # budgets: head:finance on every row
    assert budgets["R-05"]["dept"] == "finance" and budgets["R-01"]["dept"] is None  # no leak of hidden rows
    assert [x["dept"] for x in s["public"]["staff_by_dept"]] == ["finance"]


async def test_ceo_sees_all_with_suppression(personas):
    s = await headcount_summary(personas["ceo"])
    assert _reqs(s) == {f"R-0{i}" for i in range(1, 7)}
    staff = {x["dept"]: x for x in s["public"]["staff_by_dept"]}
    assert staff["exec"]["employees"] is None and staff["exec"]["suppressed"] is True
    assert staff["eng"]["employees"] == _true_counts()["eng"]
    assert len(s["budget"]["items"]) == 6


async def test_hr_staff_get_roles_but_no_budgets_and_no_staff_counts(personas):
    s = await headcount_summary(personas["hr_staff"])
    assert _reqs(s) == {f"R-0{i}" for i in range(1, 7)}
    assert s["level"] == 2 and s["budget"]["allowed"] is False and s["budget"]["items"] == []
    assert {d["req_id"] for d in s["budget"]["denied"]} == _reqs(s)  # explicit, not skipped
    assert s["public"]["staff_by_dept"] == []
    assert {x["dept"] for x in s["public"]["staff_denied"]} == {"sales", "eng", "legal", "finance", "hr", "exec", "it"}


@pytest.mark.parametrize("who", ["sales_staff", "eng_staff", "exec_staff", "platform_admin", "auditor"])
async def test_staff_outside_hr_cannot_start_it(personas, who):
    with pytest.raises(NoPermission):
        await headcount_summary(personas[who])


@pytest.mark.parametrize("who", ["sales_staff", "eng_staff", "hr_staff", "platform_admin", "auditor", "legal_staff"])
async def test_sql_aggregate_returns_nothing_to_non_heads(personas, who):
    async with pg.principal_tx(personas[who]) as conn:
        cur = await conn.execute("SELECT * FROM hr.headcount_by_dept()")
        assert await cur.fetchall() == []


async def test_sql_aggregate_never_returns_a_small_count(personas):
    for who in ("ceo", "hr_head", "legal_head", "eng_head"):
        async with pg.principal_tx(personas[who]) as conn:
            cur = await conn.execute("SELECT * FROM hr.headcount_by_dept()")
            for row in await cur.fetchall():
                assert row["employees"] is None or row["employees"] >= 5, (who, row)
    async with pg.principal_tx(personas["legal_head"]) as conn:  # legal has 4 people: suppressed
        rows = await (await conn.execute("SELECT * FROM hr.headcount_by_dept()")).fetchall()
        assert rows == [{"dept": "legal", "employees": None, "suppressed": True}]


async def test_forged_role_gets_no_aggregate(personas):
    async with pg.principal_tx(personas["sales_staff"]) as conn:
        await conn.execute("SELECT set_config('app.role', 'ceo', true)")
        assert await (await conn.execute("SELECT * FROM hr.headcount_by_dept()")).fetchall() == []
