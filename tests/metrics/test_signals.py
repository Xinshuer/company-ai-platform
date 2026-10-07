"""Signal detectors run with the requester's identity: different people get different evidence, and a
requester who cannot read a source gets an explicit "no permission", never "nothing found"."""

from __future__ import annotations

import pytest

from corpai.metrics import SIGNAL_KINDS, detect, detect_all
from corpai.types import Level
from tests.metrics.conftest import owner_rows

pytestmark = pytest.mark.anyio


async def test_ceo_and_cto_get_different_project_evidence(seeded, personas):
    ceo = await detect(personas["ceo"], "project_testing_delayed")
    cto = await detect(personas["eng_head"], "project_testing_delayed")
    assert ceo.refs == cto.refs == ["P-02"]
    # the CEO sees the project's stage but not its CI runs or tickets (proj:<id>, head:eng only)
    assert set(ceo.denied_sources) == {"eng.ci_runs", "eng.tickets"}
    assert ceo.findings[0]["ci_visible"] is False and ceo.findings[0]["trailing_failed_runs"] is None
    f = cto.findings[0]
    assert f["ci_visible"] and f["trailing_failed_runs"] >= 5 and f["ci_red"] and f["claim_ticket_ids"]
    assert f["inconsistent"] is True
    assert cto.denied_sources == ()


@pytest.mark.parametrize("who,kind,denied", [
    ("sales_staff", "overdue_receivable", "fin.invoices"),
    ("eng_staff", "contract_expiring", "legal.contracts"),
    ("sales_staff", "hiring_over_budget", "hr.requisition_budgets"),
    ("eng_head", "dept_budget_overrun", "fin.journal_entries"),
    ("finance_staff", "missing_dpa_clause", "legal.contracts"),
    ("legal_staff", "project_testing_delayed", "eng.projects"),
    ("platform_admin", "overdue_receivable", "fin.invoices"),
])
async def test_no_permission_is_explicit(seeded, personas, who, kind, denied):
    res = await detect(personas[who], kind)
    assert res.status == "no_permission" and not res.findings and not res.found
    assert denied in res.denied_sources and "no permission" in res.message
    assert res.level == Level.L1


async def test_finance_sees_overdue_without_crm_details(seeded, personas):
    fin = await detect(personas["finance_staff"], "overdue_receivable")
    assert fin.refs == ["A-0001"] and "crm.accounts" in fin.denied_sources
    assert fin.findings[0]["account_name"] is None and fin.findings[0]["crm_visible"] is False
    ceo = await detect(personas["ceo"], "overdue_receivable")
    assert ceo.findings[0]["account_name"] and ceo.findings[0]["tier"] == "strategic"
    assert ceo.level >= Level.L3


async def test_detect_all_runs_every_detector(seeded, personas):
    out = await detect_all(personas["ceo"])
    assert set(out) == set(SIGNAL_KINDS)
    assert all(r.status in ("ok", "no_permission") for r in out.values())


async def test_findings_name_no_employee(seeded, personas):
    """D7: evidence carries business ids and aggregates, never a person's name or user id."""
    people = {r["name"] for r in owner_rows("SELECT name FROM core.users")}
    ids = {r["user_id"] for r in owner_rows("SELECT user_id FROM core.users")}
    for who in ("ceo", "eng_head", "finance_head", "legal_head", "hr_head"):
        for res in (await detect_all(personas[who])).values():
            for f in res.findings:
                for v in f.values():
                    vals = v if isinstance(v, list) else [v]
                    for x in vals:
                        assert x not in people and x not in ids, (who, res.kind, x)


async def test_unknown_signal_is_refused(seeded, personas):
    with pytest.raises(ValueError):
        await detect(personas["ceo"], "salary_outliers")
