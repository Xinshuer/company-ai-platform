"""M4 sales: company profiling (web, L1, external), fit and intent scores (code), tiers, playbook, backtest.

Public API (the wiring phase calls these from the sales_profile / sales_web graphs and API routes):
    CompanyProfile                                              fixed-field public profile (D5: companies only)
    async extract_profile(p, *, account_id, pages, run_id)      one tool-less call, purpose sales.web_extract
    async extract_profile_with_meta(...) -> (profile, ExtractMeta)   + route, flags, dropped fields
    load_web_pages(path=None), pages_for(account_id, pages)     the mock web corpus
    fit_score(profile) -> (0-100, reasons)                      public signals only
    async intent_score(p, account_id, *, as_of=None, run_id=None) -> (0-100, reasons)   CRM via RLS; NoPermission
    tier(fit, intent) -> "high" | "medium" | "low"
    select_playbook(tier) -> PlaybookSelection                  corpai/sales/playbook.yaml (L3, owner head:sales)
    backtest(as_of) -> report; backtest_rows(...) (pure); auc(pos, neg)
    register_fake_responders()                                  FakeLLM 'sales.' responders (done on import)
    NoPermission
"""

from __future__ import annotations

from corpai.sales.backtest import auc, backtest, backtest_rows, profile_from_pages
from corpai.sales.errors import NoPermission
from corpai.sales.extract import (
    ExtractMeta,
    extract_profile,
    extract_profile_with_meta,
    load_web_pages,
    pages_for,
)
from corpai.sales.fake import register_fake_responders
from corpai.sales.intent import intent_score
from corpai.sales.playbook import PlaybookSelection, load_playbook, select_playbook
from corpai.sales.profile import (
    CompanyProfile,
    EmployeesBand,
    FleetBand,
    FundingStage,
    GrowthSignal,
    Industry,
    TechSignal,
    validate_profile,
)
from corpai.sales.scoring import MARKETS, fit_score, intent_from_rows, tier

# Fake mode must work offline out of the box (tests, demo without a key). Registering into the
# FakeLLM singleton is harmless in live mode: that backend is simply never used.
register_fake_responders()

__all__ = [
    "MARKETS", "CompanyProfile", "EmployeesBand", "ExtractMeta", "FleetBand", "FundingStage", "GrowthSignal",
    "Industry", "NoPermission", "PlaybookSelection", "TechSignal", "auc", "backtest", "backtest_rows",
    "extract_profile", "extract_profile_with_meta", "fit_score", "intent_from_rows", "intent_score",
    "load_playbook", "load_web_pages", "pages_for", "profile_from_pages", "register_fake_responders",
    "select_playbook", "tier", "validate_profile",
]
