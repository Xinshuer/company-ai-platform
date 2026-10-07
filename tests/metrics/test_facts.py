"""Ids and dates in the writer's text must be ones it was shown; every issue must be named."""

from __future__ import annotations

import pytest

from corpai.metrics.facts import allowed_facts, missing_issues, verify_facts
from corpai.metrics.signals import SignalResult

SHOWN = ("Report: 2026-Q3 管理层报告\n"
         "signal.overdue_receivable.A-0001.total_overdue_eur | x | EUR 1\n"
         "- contract_expiring | Contract expiring | ref C-0003 | expires_on 2026-11-30; notice_deadline 2026-10-31\n")


@pytest.mark.parametrize("text", [
    "C-0003 expires on 2026-11-30.", "Notice by 2026年10月31日.", "Notice by 10月31日.", "Due in October 2026.",
    "Due in 2026-Q4.", "Due in Q4 2026.", "In 2026, A-0001 paid late.", "The report for 2026-Q3 and 2026-09.",
    "Placeholders are not facts: {metric:signal.overdue_receivable.A-0001.total_overdue_eur}.",
])
def test_shown_facts_pass_in_any_form(text):
    assert verify_facts(text, {}, SHOWN) == []


@pytest.mark.parametrize("text,token", [
    ("C-0007 expires next month.", "C-0007"),
    ("Notice deadline 2026-10-30.", "2026-10-30"),
    ("Notice deadline 2026年10月30日.", "2026年10月30日"),
    ("Notice deadline 10月30日.", "10月30日"),
    ("Overdue since March 2026.", "March 2026"),
    ("Overdue since 2019.", "2019"),
    ("Planned for 2027-Q1.", "2027-Q1"),
    ("Invoice INV-00042 is open.", "INV-00042"),
])
def test_invented_ids_and_dates_are_problems(text, token):
    problems = verify_facts(text, {}, SHOWN)
    assert [p.token for p in problems] == [token] and problems[0].kind == "unverified_fact"


def test_allowed_dates_include_their_month_quarter_and_year():
    a = allowed_facts(SHOWN)
    assert {"2026-10-31", "2026-11-30"} <= a["day"] and {"2026-10", "2026-11", "2026-07"} <= a["month"]
    assert {"2026-Q3", "2026-Q4"} <= a["quarter"] and a["year"] == {"2026"} and {"A-0001", "C-0003"} <= a["id"]


def test_every_issue_the_reader_may_see_must_be_named():
    signals = {
        "contract_expiring": SignalResult("contract_expiring", "ok", "2026-10-01", ({"ref": "C-0003"},)),
        "dept_budget_overrun": SignalResult("dept_budget_overrun", "ok", "2026-10-01", ({"ref": "sales"},)),
        "missing_dpa_clause": SignalResult("missing_dpa_clause", "no_permission", "2026-10-01"),
    }
    assert [p.token for p in missing_issues("C-0003 and sales.", signals)] == []
    # a ref inside a longer id or word does not count
    assert [p.token for p in missing_issues("C-00031 and wholesales.", signals)] == ["C-0003", "sales"]
    assert all(p.kind == "missing_issue" for p in missing_issues("", signals))
