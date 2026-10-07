"""Fit (public signals) and intent (CRM, RLS) scores, tiers, the playbook and the backtest."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from corpai.sales import (
    CompanyProfile,
    NoPermission,
    backtest,
    fit_score,
    intent_from_rows,
    intent_score,
    select_playbook,
    tier,
)
from corpai.sales.playbook import PATH, PlaybookError, load_playbook
from corpai.types import Level
from tests.sales.conftest import owner_rows

STRONG = CompanyProfile(company="Elbstrom Paketdienst GmbH", industry="parcel_delivery", hq_country="DE",
                        countries=("DE",), fleet_size_band="150-499", funding_stage="growth",
                        growth_signals=("software_tender", "new_site", "fleet_expansion"),
                        tech_signals=("evaluating_planning_software",))
WEAK = CompanyProfile(company="Somewhere Retail Inc", industry="not_logistics", hq_country="GB",
                      fleet_size_band="1-49")


# ---- fit ----------------------------------------------------------------------------------

def test_fit_rewards_public_signals():
    strong, reasons = fit_score(STRONG)
    weak, _ = fit_score(WEAK)
    assert 0 <= weak < 30 < 70 <= strong <= 100
    assert all(isinstance(r, str) and len(r) < 80 for r in reasons)


def _with(**change) -> CompanyProfile:
    return CompanyProfile.model_validate({**STRONG.model_dump(), **change})


def test_fit_components_move_the_score_in_the_right_direction():
    base, _ = fit_score(STRONG)
    assert fit_score(_with(hq_country="GB", countries=("GB",)))[0] < base
    assert fit_score(_with(hq_country="CH", countries=("CH", "DE")))[0] < base  # operates in a market: partial
    assert fit_score(_with(fleet_size_band="1-49"))[0] < base
    assert fit_score(_with(industry="unknown"))[0] < base
    assert fit_score(_with(growth_signals=(), tech_signals=()))[0] < base


def test_unknown_profile_scores_low():
    assert fit_score(CompanyProfile(company="unknown company"))[0] == 0


# ---- intent -------------------------------------------------------------------------------

def _ix(day: str, sentiment: str = "positive", kind: str = "meeting") -> dict:
    return {"occurred_on": date.fromisoformat(day), "sentiment": sentiment, "kind": kind}


def test_intent_rows_only_use_data_before_as_of():
    as_of = date(2026, 6, 1)
    before = [_ix("2026-05-20"), _ix("2026-05-01", "neutral", "email")]
    after = [_ix("2026-06-01"), _ix("2026-07-15")]  # on and after the cut-off: must be ignored
    assert intent_from_rows(before + after, [], [], as_of=as_of) == intent_from_rows(before, [], [], as_of=as_of)


def test_intent_rows_recency_sentiment_pipeline():
    as_of = date(2026, 10, 1)
    hot = [_ix("2026-09-25"), _ix("2026-09-10", kind="demo"), _ix("2026-08-20", kind="call")]
    cold = [_ix("2026-01-10", "negative", "email")]
    hot_score, reasons = intent_from_rows(hot, [{"stage": "negotiation"}], [{"valid_until": date(2026, 12, 1)}],
                                          as_of=as_of)
    cold_score, _ = intent_from_rows(cold, [{"stage": "closed_lost"}], [], as_of=as_of)
    assert hot_score >= 80 and cold_score <= 10
    assert any(r.startswith("pipeline:negotiation") for r in reasons)
    negative = [_ix("2026-09-25", "negative"), _ix("2026-09-10", "negative")]
    assert intent_from_rows(negative, [], [], as_of=as_of)[0] < intent_from_rows(hot[:2], [], [], as_of=as_of)[0]
    # the backtest variant ignores stages and quotes (no history: they would leak the outcome)
    assert intent_from_rows(hot, [{"stage": "negotiation"}], [], as_of=as_of, use_pipeline=False)[0] < hot_score


@pytest.mark.anyio
async def test_intent_for_own_account(personas, fake_llm):
    # A-0002 is owned by sales_staff (company script)
    score, reasons = await intent_score(personas["sales_staff"], "A-0002")
    assert 0 <= score <= 100 and reasons
    assert fake_llm.calls == []  # intent is code, never a model call
    # reasons are codes and numbers, never CRM text
    summaries = [r["summary"] for r in owner_rows("SELECT summary FROM crm.interactions WHERE account_id = 'A-0002'")]
    assert not [s for s in summaries if any(s[:30] in r for r in reasons)]


@pytest.mark.anyio
@pytest.mark.parametrize("who", ["sales_head", "ceo"])
async def test_intent_head_and_ceo_see_every_account(personas, who):
    for account in ("A-0001", "A-0002", "A-0010"):
        score, _ = await intent_score(personas[who], account)
        assert 0 <= score <= 100


@pytest.mark.anyio
async def test_intent_refused_for_an_account_the_user_cannot_see(personas, fake_llm):
    # A-0001 belongs to another rep: explicit no-permission, never a score from partial data
    with pytest.raises(NoPermission, match="no permission"):
        await intent_score(personas["sales_staff"], "A-0001", run_id="run_intent_denied_0001")
    with pytest.raises(NoPermission, match="no permission"):  # same answer for a non-existent id
        await intent_score(personas["sales_staff"], "A-9999")
    rows = owner_rows("SELECT * FROM audit.log WHERE run_id = 'run_intent_denied_0001'")
    assert rows and rows[-1]["event"] == "policy_decision" and rows[-1]["decision"] == "deny"
    assert fake_llm.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("who", ["legal_staff", "eng_staff", "hr_head", "platform_admin", "auditor", "exec_staff"])
async def test_intent_refused_outside_sales(personas, who):
    # legal_staff holds grant:crm:A-0003 (RLS shows the account) but not the interaction tools
    with pytest.raises(NoPermission):
        await intent_score(personas[who], "A-0003")


@pytest.mark.anyio
async def test_intent_legal_grant_sees_account_but_gets_no_intent(personas):
    from corpai.db import pg

    async with pg.principal_tx(personas["legal_staff"]) as conn:
        cur = await conn.execute("SELECT account_id FROM crm.accounts")
        assert [r["account_id"] for r in await cur.fetchall()] == ["A-0003"]
    with pytest.raises(NoPermission):
        await intent_score(personas["legal_staff"], "A-0003")


# ---- tiers and playbook -------------------------------------------------------------------

@pytest.mark.parametrize("fit,intent,expected", [
    (90, 90, "high"), (50, 60, "high"), (49, 95, "medium"), (80, 59, "medium"), (10, 35, "medium"),
    (70, 20, "medium"), (69, 20, "low"), (100, 19, "low"), (0, 0, "low"), (30, 34, "low"),
])
def test_tier_table(fit, intent, expected):
    assert tier(fit, intent) == expected


@pytest.mark.parametrize("bad", [(-1, 50), (50, 101)])
def test_tier_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        tier(*bad)


def test_playbook_selection_per_tier():
    picks = {t: select_playbook(t) for t in ("high", "medium", "low")}
    for p in picks.values():
        assert p.ref == "sales.followup@3" and p.level is Level.L3 and p.owner_group == "head:sales"
        assert p.allowed_commitments and p.forbidden_commitments
        assert not set(p.allowed_commitments) & set(p.forbidden_commitments)
    assert picks["high"].cadence_days < picks["medium"].cadence_days < picks["low"].cadence_days
    assert "pilots" in picks["medium"].forbidden_commitments
    with pytest.raises(ValueError):
        select_playbook("urgent")


def test_playbook_must_be_active_and_owned_by_sales_head(tmp_path: Path):
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    for change in ({"status": "draft"}, {"owner_group": "dept:sales"}, {"tiers": {"high": raw["tiers"]["high"]}}):
        f = tmp_path / f"pb_{len(change)}_{list(change)[0]}.yaml"
        f.write_text(yaml.safe_dump({**raw, **change}), encoding="utf-8")
        with pytest.raises(PlaybookError):
            load_playbook(f)


# ---- backtest -----------------------------------------------------------------------------

def test_backtest_runs_on_the_seeded_company(seeded):
    report = backtest(date(2026, 1, 1))
    assert report["metric"] == "roc_auc" and report["n"] == report["won"] + report["lost"] > 0
    for r in report["rows"]:
        assert 0 <= r["fit"] <= 100 and 0 <= r["intent"] <= 100 and r["tier"] in ("high", "medium", "low")
        assert date(2026, 1, 1) <= date.fromisoformat(owner_rows(
            "SELECT close_date::text AS d FROM crm.opportunities WHERE opp_id = %s", (r["opp_id"],))[0]["d"])
    last = owner_rows("SELECT * FROM audit.log WHERE event = 'owner_job' AND actor = 'svc:sales_backtest' "
                      "ORDER BY seq DESC LIMIT 1")
    assert last and "rows" not in last[0]["details"]  # metadata only in the audit log
    with pytest.raises(ValueError):
        backtest(date(2026, 10, 1))
