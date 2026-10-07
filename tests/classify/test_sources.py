"""source_default: config/sources.yaml, first match wins, unknown → L4."""

from __future__ import annotations

import pytest

from corpai.classify import source_default
from corpai.classify import sources as sources_mod
from corpai.types import Level

L1, L2, L3, L4 = Level.L1, Level.L2, Level.L3, Level.L4


@pytest.mark.parametrize("source,level", [
    # L1
    ("public.site", L1), ("public.press", L1), ("public.jobs", L1), ("web.search", L1), ("web.fetch", L1),
    ("legal.regulations", L1), ("legal.regulation:GDPR-9", L1), ("public", L1), ("legal_regs", L1),
    # L2
    ("general.handbook", L2), ("general.templates", L2), ("dept.eng.wiki", L2), ("dept.sales.runbook", L2),
    ("hr.requisitions", L2), ("hr.requisition:R-004", L2), ("general", L2),
    # L3
    ("crm.accounts", L3), ("crm.account:A-0012", L3), ("crm.contacts", L3), ("crm.quotes", L3),
    ("eng.tickets", L3), ("eng.ticket:FP-1234", L3), ("eng.repo", L3), ("legal.contracts", L3),
    ("legal.contract_requests", L3), ("fin.journal_entries", L3), ("fin.invoices", L3), ("fin.budgets", L3),
    ("hr.employees", L3), ("hr.candidates", L3), ("hr.requisition_budgets", L3), ("playbooks.sales", L3),
    ("sales", L3), ("finance", L3),
    # L4: specific beats general
    ("eng.vulnerabilities", L4), ("eng.vuln:V-2", L4), ("legal.matters", L4), ("legal.matter:M-0002", L4),
    ("fin.statements", L4), ("hr.compensation", L4), ("hr.compensation:u0012", L4), ("hr.performance", L4),
    ("hr.screenings", L4), ("exec.board", L4), ("exec", L4),
])
def test_source_table(source, level):
    assert source_default(source) == level


@pytest.mark.parametrize("source", ["", "   ", "unknown.system", "crm", "legal.something_new", "publicity.x",
                                    "xcrm.accounts", "audit.log", None, 42])
def test_unknown_sources_are_restricted(source):
    assert source_default(source) == L4  # type: ignore[arg-type]


def test_case_insensitive():
    assert source_default("CRM.Accounts") == L3
    assert source_default("Public.Site") == L1


def test_specific_patterns_come_before_general_ones():
    """Guard the ordering in sources.yaml: a general glob above a specific one would downgrade it."""
    rules = sources_mod._rules()
    import fnmatch
    for i, (pat, level) in enumerate(rules):
        for later_pat, later_level in rules[i + 1:]:
            # if an earlier pattern shadows a later literal pattern, the levels must agree
            if not any(ch in later_pat for ch in "*?[") and fnmatch.fnmatchcase(later_pat, pat):
                assert level == later_level, (pat, later_pat)


def test_broken_config_fails_closed(monkeypatch):
    def boom(name):
        raise OSError("config missing")

    monkeypatch.setattr(sources_mod, "load_yaml", boom)
    assert source_default("public.site") == L4


def test_bad_level_in_config_fails_closed(monkeypatch):
    monkeypatch.setattr(sources_mod, "load_yaml", lambda name: {"sources": [{"pattern": "public.*", "level": "L0"}]})
    assert source_default("public.site") == L4
