"""Every planted issue is found by its detector, with the planted evidence, and nothing else is flagged
(the detection-rate check of design-review 二.13 for the management report)."""

from __future__ import annotations

import pytest

from corpai.metrics import detect

pytestmark = [pytest.mark.eval, pytest.mark.anyio]


async def test_overdue_receivable(personas, planted):
    exp = planted["overdue_receivable"]
    fin = await detect(personas["finance_head"], "overdue_receivable")
    assert fin.refs == [exp["ref"]]
    f = fin.findings[0]
    assert f["invoices_over_threshold"] == exp["invoices_over_60_days"]
    assert f["amount_over_threshold_eur"] == exp["amount_over_60_days_eur"]
    assert f["total_overdue_eur"] == exp["total_overdue_eur"]
    assert f["max_days_overdue"] == exp["max_days_overdue"]
    ceo = (await detect(personas["ceo"], "overdue_receivable")).findings[0]
    assert ceo["account_name"] == exp["account_name"] and ceo["arr_eur"] == exp["arr_eur"]


async def test_contract_expiring(personas, planted):
    exp = planted["contract_expiring"]
    res = await detect(personas["legal_head"], "contract_expiring")
    assert res.refs == exp["signed_contracts_expiring_2026_11"] == [exp["ref"]]
    f = res.findings[0]
    assert (f["expires_on"], f["auto_renew"], f["notice_days"], f["notice_deadline"]) == \
        (exp["expires_on"], exp["auto_renew"], exp["notice_days"], exp["notice_deadline"])
    assert f["account_id"] == exp["account_id"] and not f["notice_deadline_passed"]


async def test_project_testing_delayed(personas, planted):
    exp = planted["project_testing_delayed"]
    res = await detect(personas["eng_head"], "project_testing_delayed")
    assert res.refs == [exp["ref"]]
    f = res.findings[0]
    assert (f["stage_since"], f["days_in_stage"]) == (exp["stage_since"], exp["days_in_stage"])
    assert f["trailing_failed_runs"] == exp["trailing_failed_runs"]
    assert f["last_5_run_ids"] == exp["last_5_run_ids"]
    assert f["claim_ticket_ids"] == [exp["claim_ticket_id"]] and f["inconsistent"]


async def test_hiring_over_budget(personas, planted):
    exp = planted["hiring_over_budget"]
    res = await detect(personas["hr_head"], "hiring_over_budget")
    assert res.refs == exp["reqs_over_budget"] == [exp["ref"]]
    f = res.findings[0]
    assert (f["approved_budget_eur"], f["projected_cost_eur"], f["over_pct"]) == \
        (exp["approved_budget_eur"], exp["projected_cost_eur"], exp["over_pct"])


async def test_dept_budget_overrun(personas, planted):
    exp = planted["dept_budget_overrun"]
    res = await detect(personas["finance_head"], "dept_budget_overrun")  # default threshold 10 %, last quarter
    assert res.refs == [exp["ref"]]
    strict = await detect(personas["finance_head"], "dept_budget_overrun", threshold_pct=15.0)
    assert strict.refs == exp["depts_over_15_pct"]
    f = res.findings[0]
    assert f["period"] == exp["quarter"] and f["periods"] == exp["periods"]
    assert (f["actual_eur"], f["budget_eur"], f["overrun_pct"]) == (exp["actual_eur"], exp["budget_eur"],
                                                                     exp["overrun_pct"])


async def test_missing_dpa_clause(personas, planted):
    exp = planted["missing_dpa_clause"]
    res = await detect(personas["legal_head"], "missing_dpa_clause")
    assert res.refs == exp["contracts_with_gap"] == [exp["ref"]]
    assert res.findings[0]["account_id"] == exp["account_id"]


async def test_detection_rate_is_complete(personas, planted):
    who = {"overdue_receivable": "ceo", "contract_expiring": "legal_head", "project_testing_delayed": "eng_head",
           "hiring_over_budget": "hr_head", "dept_budget_overrun": "finance_head", "missing_dpa_clause": "legal_head"}
    found = {k: planted[k]["ref"] in (await detect(personas[p], k)).refs for k, p in who.items()}
    assert all(found.values()), found
