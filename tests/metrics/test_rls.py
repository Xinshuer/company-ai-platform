"""Who sees which metric: RLS decides, get_metric reports "no permission or not published" explicitly."""

from __future__ import annotations

from dataclasses import replace

import pytest

from corpai.metrics import NO_ACCESS, get_breakdown, get_metric, list_metrics
from corpai.policy import decide
from corpai.types import Action, Label, Level, Resource
from tests.metrics.conftest import PUBLISHED_PERIOD as PUB
from tests.metrics.conftest import owner_rows, rls_rows

pytestmark = pytest.mark.security

DRAFT = "2026-09"


@pytest.mark.anyio
async def test_sales_staff_sees_published_company_metrics_only(drafts, published, personas):
    p = personas["sales_staff"]
    mv = await get_metric(p, "revenue_recognised", PUB)
    assert mv.status == "published" and mv.value is not None and mv.level == Level.L2
    assert mv.provenance["signed_by"] == personas["finance_head"].user_id
    draft = await get_metric(p, "revenue_recognised", DRAFT)
    assert draft.status == "unavailable" and draft.value is None and draft.reason == NO_ACCESS
    for mid, dims in [("cash_net_flow", None), ("ar_overdue_60d", None), ("opex_actual", {"dept": "eng"}),
                      ("hiring_budget_approved", {"dept": "sales"}), ("opex_actual", None)]:
        got = await get_metric(p, mid, PUB, dims)
        assert got.status == "unavailable" and got.value is None, (mid, dims)
    rows = rls_rows(p, "SELECT status, acl_groups FROM metrics.snapshots")
    assert rows and all(r["status"] == "published" for r in rows)
    for r in rows:
        # company-wide ("all") or the sales department's own published rows; nothing else
        assert set(r["acl_groups"]) & p.groups <= {"all", "dept:sales"}


@pytest.mark.anyio
async def test_finance_staff_sees_drafts(drafts, published, personas):
    p = personas["finance_staff"]
    mv = await get_metric(p, "revenue_recognised", DRAFT)
    assert mv.status == "draft" and mv.value is not None and mv.level == Level.L3
    assert (await get_metric(p, "attrition_pct", DRAFT, {"dept": "eng"})).level == Level.L4
    # hiring budgets are narrowed to the CFO and the CEO even as drafts
    assert (await get_metric(p, "hiring_budget_approved", DRAFT)).status == "unavailable"


@pytest.mark.anyio
async def test_ceo_sees_drafts_and_every_published_audience(drafts, published, personas):
    p = personas["ceo"]
    assert (await get_metric(p, "hiring_budget_approved", DRAFT)).status == "draft"
    for mid, dims in [("opex_actual", {"dept": "eng"}), ("opex_actual", {"dept": "sales"}), ("cash_net_flow", None),
                      ("ci_pass_rate_pct", {"project_id": "P-02"}), ("headcount", {"dept": "hr"})]:
        assert (await get_metric(p, mid, PUB, dims)).status == "published", (mid, dims)
    visible = {r["metric_id"] for r in rls_rows(p, "SELECT DISTINCT metric_id FROM metrics.snapshots")}
    assert visible >= {"revenue_recognised", "hiring_variance_pct", "contracts_missing_dpa"}


@pytest.mark.anyio
async def test_eng_head_sees_eng_dept_metrics(drafts, published, personas):
    p = personas["eng_head"]
    eng = await get_metric(p, "opex_actual", PUB, {"dept": "eng"})
    assert eng.status == "published" and eng.value is not None
    ci = await get_breakdown(p, "ci_pass_rate_pct", PUB)
    assert {tuple(m.dims.items()) for m in ci} >= {(("project_id", "P-02"),)}
    assert (await get_metric(p, "opex_actual", PUB, {"dept": "sales"})).status == "unavailable"
    assert (await get_metric(p, "opex_actual", DRAFT, {"dept": "eng"})).status == "unavailable"
    assert (await get_metric(p, "cash_net_flow", PUB)).status == "published"   # management audience
    assert (await get_metric(p, "contracts_missing_dpa", PUB)).status == "unavailable"  # legal's


@pytest.mark.anyio
async def test_eng_staff_sees_eng_rows_but_not_management_ones(published, personas):
    p = personas["eng_staff"]
    assert (await get_metric(p, "ci_pass_rate_pct", PUB)).status == "published"
    assert (await get_metric(p, "cash_net_flow", PUB)).status == "unavailable"
    assert (await get_metric(p, "hiring_budget_approved", PUB, {"dept": "eng"})).status == "unavailable"


@pytest.mark.anyio
async def test_deactivated_user_sees_nothing(published, personas):
    p = replace(personas["ceo"], active=False)
    assert (await get_metric(p, "revenue_recognised", PUB)).status == "unavailable"
    with pytest.raises(PermissionError):
        await list_metrics(p)


@pytest.mark.anyio
async def test_list_metrics_shows_definitions_and_access(drafts, published, personas):
    listed = {m["metric_id"]: m for m in await list_metrics(personas["sales_staff"])}
    assert "sql" not in listed["revenue_recognised"]
    assert listed["revenue_recognised"]["periods"] == [{"period": PUB, "statuses": ["published"]}]
    assert listed["cash_net_flow"]["access"] == NO_ACCESS
    ceo = {m["metric_id"]: m for m in await list_metrics(personas["ceo"])}
    assert {x["period"] for x in ceo["revenue_recognised"]["periods"]} >= {PUB, DRAFT}


def test_rls_agrees_with_policy_on_every_snapshot(drafts, published, personas):
    rows = owner_rows("SELECT metric_id, period, dims_key, version, level, acl_groups FROM metrics.snapshots")
    for key in ("ceo", "sales_staff", "sales_head", "eng_head", "eng_staff", "finance_head", "finance_staff",
                "hr_head", "legal_staff", "platform_admin", "auditor"):
        p = personas[key]
        seen = {(r["metric_id"], r["period"], r["dims_key"], r["version"])
                for r in rls_rows(p, "SELECT metric_id, period, dims_key, version FROM metrics.snapshots")}
        for r in rows:
            res = Resource(kind="fin.metric", id=r["metric_id"],
                           label=Label(level=Level(r["level"]), acl_groups=frozenset(r["acl_groups"])))
            allowed = decide(p, Action.READ, res).allow
            assert allowed == ((r["metric_id"], r["period"], r["dims_key"], r["version"]) in seen), (key, r)


@pytest.mark.anyio
async def test_unknown_dimension_is_an_error(published, personas):
    with pytest.raises(ValueError):
        await get_metric(personas["ceo"], "revenue_recognised", PUB, {"employee_id": "u0011"})
