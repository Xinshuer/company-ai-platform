"""Fit and intent scores as plain code (decision D5, design-review 三 销售).

- **fit** (0-100) says whether a company suits FleetPilot. Inputs: the public CompanyProfile only.
- **intent** (0-100) says whether it is likely to buy now. Inputs: CRM rows only (L3).

The two never meet in a model call: fit reads no CRM data and intent reads no web text, so an
injected page cannot move intent and a CRM fact cannot leave through the external route. Reasons
are short codes with numbers, never copied CRM or page text.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Mapping

from corpai.sales.profile import CompanyProfile, FleetBand, FundingStage, GrowthSignal, Industry, TechSignal

# FleetPilot's markets (company script: customers in DE, NL, FR, PL, AT, IT, ES).
MARKETS: frozenset[str] = frozenset({"DE", "NL", "FR", "PL", "AT", "IT", "ES"})

# Route-dense operations get the most from route planning; other logistics still fit.
_INDUSTRY_POINTS: dict[Industry, int] = {
    Industry.PARCEL_DELIVERY: 25, Industry.EXPRESS_COURIER: 25, Industry.COLD_CHAIN_FOOD: 25,
    Industry.REFRIGERATED_TRANSPORT: 22, Industry.RETAIL_DISTRIBUTION: 22, Industry.FREIGHT_FORWARDING: 20,
    Industry.BEVERAGE_DISTRIBUTION: 20, Industry.BUILDING_SUPPLIES: 18, Industry.REGIONAL_HAULAGE: 18,
    Industry.CONSTRUCTION_LOGISTICS: 15, Industry.OTHER_LOGISTICS: 12,
}
# Mid-size and large fleets (company script); tiny fleets rarely pay for a planning suite.
_FLEET_POINTS: dict[FleetBand, int] = {
    FleetBand.B1_49: 5, FleetBand.B50_149: 15, FleetBand.B150_499: 25, FleetBand.B500_999: 25, FleetBand.B1000: 22,
}
_FUNDING_POINTS: dict[FundingStage, int] = {
    FundingStage.GROWTH: 10, FundingStage.VENTURE: 10, FundingStage.PRIVATE_EQUITY: 8, FundingStage.PUBLIC: 6,
    FundingStage.BOOTSTRAPPED: 4, FundingStage.SEED: 4,
}
_GROWTH_POINTS: dict[GrowthSignal, int] = {
    GrowthSignal.SOFTWARE_TENDER: 8, GrowthSignal.NEW_SITE: 5, GrowthSignal.FLEET_EXPANSION: 5,
    GrowthSignal.EV_TRIAL: 4, GrowthSignal.NEW_CONTRACT: 4, GrowthSignal.FUNDING_ROUND: 3, GrowthSignal.HIRING: 2,
}
_TECH_BONUS = {TechSignal.EVALUATING_PLANNING_SOFTWARE: 3, TechSignal.LEGACY_PLANNING: 2}


def fit_score(profile: CompanyProfile) -> tuple[int, list[str]]:
    """0-100 from public signals: industry ≤25, fleet ≤25, market ≤20, funding ≤10, growth/tech ≤20."""
    reasons: list[str] = []
    pts = _INDUSTRY_POINTS.get(profile.industry, 0)
    reasons.append(f"industry:{profile.industry.value} +{pts}")
    total = pts

    pts = _FLEET_POINTS.get(profile.fleet_size_band, 0)
    reasons.append(f"fleet:{profile.fleet_size_band.value} +{pts}")
    total += pts

    in_market = [c for c in profile.countries if c in MARKETS]
    if profile.hq_country in MARKETS:
        pts = 20
    elif in_market:
        pts = 12
    else:
        pts = 0
    reasons.append(f"market:{profile.hq_country or 'unknown'} +{pts}")
    total += pts

    pts = _FUNDING_POINTS.get(profile.funding_stage, 0)
    reasons.append(f"funding:{profile.funding_stage.value} +{pts}")
    total += pts

    growth = sum(_GROWTH_POINTS.get(s, 0) for s in set(profile.growth_signals))
    growth += sum(_TECH_BONUS.get(s, 0) for s in set(profile.tech_signals))
    growth = min(growth, 20)
    reasons.append(f"signals:{len(set(profile.growth_signals))}g/{len(set(profile.tech_signals))}t +{growth}")
    total += growth
    return max(0, min(100, total)), reasons


# ---- intent ---------------------------------------------------------------------------------

STAGE_POINTS = {"prospecting": 5, "qualification": 10, "proposal": 15, "negotiation": 20}
_RECENT_WINDOW = timedelta(days=90)


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def intent_from_rows(interactions: Iterable[Mapping], opportunities: Iterable[Mapping], quotes: Iterable[Mapping],
                     *, as_of: date, use_pipeline: bool = True) -> tuple[int, list[str]]:
    """0-100 from CRM rows dated *before* `as_of`.

    recency ≤25, frequency (90 days) ≤20, sentiment (90 days) ≤20, meetings/demos (90 days) ≤10,
    and with `use_pipeline`: open opportunity stage ≤20, live quote ≤5. The backtest turns the
    pipeline part off: stage and quote rows have no history, so their current values would leak
    the outcome being predicted.
    """
    reasons: list[str] = []
    past = sorted((r for r in interactions if _d(r["occurred_on"]) < as_of), key=lambda r: _d(r["occurred_on"]))
    recent = [r for r in past if _d(r["occurred_on"]) >= as_of - _RECENT_WINDOW]
    total = 0

    if past:
        days = (as_of - _d(past[-1]["occurred_on"])).days
        pts = 25 if days <= 14 else 20 if days <= 30 else 12 if days <= 60 else 6 if days <= 120 else 0
        reasons.append(f"recency:{days}d +{pts}")
    else:
        pts = 0
        reasons.append("recency:none +0")
    total += pts

    pts = min(len(recent) * 4, 20)
    reasons.append(f"frequency:{len(recent)}/90d +{pts}")
    total += pts

    if recent:
        pos = sum(1 for r in recent if r["sentiment"] == "positive")
        neg = sum(1 for r in recent if r["sentiment"] == "negative")
        balance = (pos - neg) / len(recent)  # -1 .. 1
        pts = round(10 + 10 * balance)
        reasons.append(f"sentiment:+{pos}/-{neg} +{pts}")
    else:
        pts = 0
        reasons.append("sentiment:none +0")
    total += pts

    deep = sum(1 for r in recent if r["kind"] in ("meeting", "demo"))
    pts = min(deep * 5, 10)
    reasons.append(f"meetings:{deep}/90d +{pts}")
    total += pts

    if use_pipeline:
        open_stages = [o["stage"] for o in opportunities if o["stage"] in STAGE_POINTS]
        pts = max((STAGE_POINTS[s] for s in open_stages), default=0)
        best = max(open_stages, key=lambda s: STAGE_POINTS[s], default="none")
        reasons.append(f"pipeline:{best} +{pts}")
        total += pts
        live = sum(1 for q in quotes if _d(q["valid_until"]) >= as_of)
        pts = 5 if live else 0
        reasons.append(f"quotes:{live} live +{pts}")
        total += pts
    return max(0, min(100, total)), reasons


# ---- tier ----------------------------------------------------------------------------------

def tier(fit: int, intent: int) -> str:
    """high | medium | low. Intent leads (a perfect fit that never answers is not a hot lead);
    fit gates the top tier so effort goes to companies FleetPilot can actually serve."""
    if not (0 <= fit <= 100 and 0 <= intent <= 100):
        raise ValueError("scores must be within 0..100")
    if intent >= 60 and fit >= 50:
        return "high"
    if intent >= 35 or (fit >= 70 and intent >= 20):
        return "medium"
    return "low"
