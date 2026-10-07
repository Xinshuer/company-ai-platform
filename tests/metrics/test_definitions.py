"""The YAML loader keeps the metric layer aggregate-only and inside the business data (D7)."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from corpai.config import load_yaml
from corpai.metrics.definitions import (ALLOWED_DIMS, FINANCE_ACL, MANAGEMENT_ACL, MAX_METRICS, MetricConfigError,
                                        load_metrics, parse_config)

REQUIRED = {
    "revenue_recognised", "arr", "gross_margin_pct", "opex_actual", "opex_budget", "opex_variance_pct",
    "cash_net_flow", "ar_overdue_total", "ar_overdue_60d", "ar_overdue_by_tier", "dso_days", "pipeline_value",
    "win_rate_pct", "bookings", "headcount", "open_requisitions", "hiring_cost_projected",
    "hiring_budget_approved", "hiring_variance_pct", "attrition_pct", "projects_by_stage", "ci_pass_rate_pct",
    "contracts_expiring_next_month", "contracts_missing_dpa", "cash_balance",
}


def _cfg() -> dict:
    return copy.deepcopy(load_yaml("metrics"))


def _one(cfg: dict, metric_id: str) -> dict:
    return next(m for m in cfg["metrics"] if m["id"] == metric_id)


def test_catalogue_is_complete_and_small():
    defs = load_metrics()
    assert len(defs) <= MAX_METRICS
    assert REQUIRED <= set(defs)
    for md in defs.values():
        assert md.name_zh and md.name_en and md.definition and md.formula and md.owner_role
        assert md.workflow == ("draft", "signed", "published")
        assert set(md.dims) <= ALLOWED_DIMS
        assert re.fullmatch(r"[0-9a-f]{64}", md.sql_sha256)


def test_people_derived_metrics_have_min_group_5():
    for md in load_metrics().values():
        if md.people_derived:
            assert md.min_group >= 5, md.id
    assert load_metrics()["headcount"].min_group == 5
    assert load_metrics()["attrition_pct"].min_group == 5
    # opex per dept carries the dept's payroll, so it is treated as people-derived as well
    assert load_metrics()["opex_actual"].min_group == 5


@pytest.mark.parametrize("dim", ["employee_id", "owner_id", "assignee_id", "candidate_id", "user_id", "name"])
def test_person_dimensions_are_refused(dim):
    cfg = _cfg()
    _one(cfg, "headcount")["dims"] = [dim]
    with pytest.raises(MetricConfigError, match="aggregate dimension"):
        parse_config(cfg)


@pytest.mark.parametrize("table", ["hr.compensation", "hr.performance", "hr.candidates"])
def test_pay_ratings_and_candidates_never_feed_a_metric(table):
    cfg = _cfg()
    m = _one(cfg, "headcount")
    m["sources"] = [table]
    m["sql"] = f"SELECT count(*) AS value, count(*) AS n FROM {table}"
    m["dims"] = []
    with pytest.raises(MetricConfigError, match="may not feed a metric"):
        parse_config(cfg)


def test_people_metric_without_min_group_is_refused():
    cfg = _cfg()
    _one(cfg, "headcount")["min_group"] = 1
    with pytest.raises(MetricConfigError, match="min_group >= 5"):
        parse_config(cfg)


@pytest.mark.parametrize("sql", [
    "SELECT count(*) AS value FROM synth.planted",
    "SELECT count(*) AS value FROM crm.accounts JOIN audit.log l ON true",
    "SELECT count(*) AS value FROM crm.accounts; DELETE FROM crm.accounts",
    "UPDATE crm.accounts SET arr_eur = 0",
    "SELECT set_config('app.groups', 'role:ceo', true) AS value FROM crm.accounts",
    "SELECT count(*) AS value FROM crm.accounts WHERE name = %(who)s",
])
def test_sql_outside_the_rules_is_refused(sql):
    cfg = _cfg()
    _one(cfg, "customers_active")["sql"] = sql
    with pytest.raises(MetricConfigError):
        parse_config(cfg)


def test_sources_must_match_the_sql():
    cfg = _cfg()
    _one(cfg, "arr")["sources"] = ["crm.accounts", "fin.invoices"]
    with pytest.raises(MetricConfigError, match="do not match"):
        parse_config(cfg)


def test_level_below_source_default_is_refused():
    cfg = _cfg()
    _one(cfg, "revenue_recognised")["level"] = 2  # the ledger is L3
    with pytest.raises(MetricConfigError, match="below its sources"):
        parse_config(cfg)


def test_draft_acl_can_only_narrow_the_finance_acl():
    cfg = _cfg()
    _one(cfg, "revenue_recognised")["draft_acl"] = ["dept:finance", "all"]
    with pytest.raises(MetricConfigError, match="narrow"):
        parse_config(cfg)
    assert set(load_metrics()["hiring_budget_approved"].draft_acl) == {"head:finance", "role:ceo"}
    assert load_metrics()["revenue_recognised"].draft_acl == FINANCE_ACL


@pytest.mark.parametrize("audience", ["user:u0001", "mgr-of:{dept}", "proj:P-01", "grant:crm:A-0001"])
def test_published_audience_is_aggregate_groups_only(audience):
    cfg = _cfg()
    _one(cfg, "headcount")["visible_to"] = ["head:{dept}", audience]
    with pytest.raises(MetricConfigError, match="aggregate group"):
        parse_config(cfg)


def test_too_many_metrics_are_refused():
    cfg = _cfg()
    base = _one(cfg, "arr")
    cfg["metrics"] = [dict(base, id=f"m_{i:02d}") for i in range(MAX_METRICS + 1)]
    with pytest.raises(MetricConfigError, match="at most"):
        parse_config(cfg)


def test_published_acl_by_audience():
    defs = load_metrics()
    assert defs["revenue_recognised"].published_acl(None) == ("all",)
    assert defs["cash_net_flow"].published_acl(None) == MANAGEMENT_ACL
    assert set(defs["opex_actual"].published_acl("eng")) == {"dept:eng", "head:eng", "role:ceo", "dept:finance"}
    # a company total of a per-dept metric goes to management, not to everyone
    assert defs["opex_actual"].published_acl(None) == MANAGEMENT_ACL
    assert set(defs["headcount"].published_acl("sales")) == {"dept:sales", "head:sales", "role:ceo",
                                                            "dept:finance", "head:hr"}
    assert set(defs["hiring_budget_approved"].published_acl("legal")) == {"head:legal", "head:hr",
                                                                         "head:finance", "role:ceo"}
    for md in defs.values():
        for dept in ("sales", "eng", "legal", None):
            acl = md.published_acl(dept)
            assert not any(g.startswith(("user:", "mgr-of:", "grant:", "proj:", "req:", "matter:")) for g in acl)


def test_metric_code_never_reads_the_evaluation_answers():
    root = Path(__file__).resolve().parents[2] / "corpai" / "metrics"
    for f in root.glob("*.py"):
        assert "synth." not in f.read_text(encoding="utf-8").replace("corpai.synth", ""), f.name


@pytest.mark.parametrize("sql,sources", [
    # review finding: quoted identifiers and comma joins slipped past the FROM/JOIN regex
    ('SELECT count(*) AS value FROM fin.invoices i, "synth"."planted" p', ["fin.invoices"]),
    ('SELECT count(*) AS value FROM fin.invoices i WHERE EXISTS (SELECT 1 FROM "audit".log)', ["fin.invoices"]),
    ("SELECT count(*) AS value FROM fin.invoices i, hr.employees e", ["fin.invoices"]),
    ("SELECT count(*) AS value FROM fin.invoices i, employees e", ["fin.invoices"]),  # via search_path
    ("SELECT count(*) AS value FROM fin.invoices i /* */ JOIN synth/**/.planted p ON true", ["fin.invoices"]),
])
def test_quoted_names_comma_joins_and_comments_cannot_hide_a_table(sql, sources):
    cfg = _cfg()
    m = _one(cfg, "customers_active")
    m["sql"], m["sources"] = sql, sources
    with pytest.raises(MetricConfigError):
        parse_config(cfg)


def test_explain_check_refuses_relations_outside_the_sources(seeded):
    """Second line: even SQL the text check accepted is planned by Postgres and every scanned relation
    must be a declared source (the refresh runs as the owner, without RLS)."""
    import dataclasses

    from corpai.db import pg
    from corpai.metrics.periods import parse_period
    from corpai.metrics.refresh import MetricSqlError, check_relations

    md = load_metrics()["customers_active"]
    params = parse_period("2026-09").params()
    with pg.connect_owner() as conn:
        check_relations(conn, md, params)  # the real definition passes
        for sql in ("SELECT count(*) AS value FROM crm.accounts a, synth.planted p",
                    "SELECT count(*) AS value FROM crm.accounts a WHERE EXISTS (SELECT 1 FROM audit.log)",
                    "SELECT count(*) AS value FROM crm.accounts a, hr.employees e"):
            with pytest.raises(MetricSqlError, match="not its declared sources"):
                check_relations(conn, dataclasses.replace(md, sql=sql), params)
