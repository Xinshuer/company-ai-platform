"""Backtest of the fit and intent scores against closed opportunities (design-review 三 销售, 七 M4).

For a cut-off date `as_of`, every account is scored with data *before* `as_of` only (web pages
published before it, CRM interactions before it; no opportunity stages or quotes, which have no
history) and compared with the outcome of its opportunities closed between `as_of` and the
simulation date. Metric: ROC AUC (probability that a random won opportunity scores above a random
lost one; 0.5 = no signal), for intent, fit and the combined score, plus the win rate per tier.

`backtest_rows(...)` is pure (used by evals on in-memory synthetic worlds); `backtest(as_of)` is the
owner-role job over the database (it reads every account, so it is a job, audited `owner_job`).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from corpai import clock
from corpai.sales.heuristics import extract_signals
from corpai.sales.profile import CompanyProfile, validate_profile
from corpai.sales.scoring import fit_score, intent_from_rows, tier

CLOSED = ("closed_won", "closed_lost")
FIT_WEIGHT, INTENT_WEIGHT = 0.4, 0.6


def auc(pos: Sequence[float], neg: Sequence[float]) -> float | None:
    """Mann-Whitney ROC AUC with ties counted half. None when one class is empty."""
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def combined(fit: int, intent: int) -> int:
    return round(FIT_WEIGHT * fit + INTENT_WEIGHT * intent)


def profile_from_pages(pages: list[dict], *, fallback_company: str = "unknown company") -> CompanyProfile:
    """The deterministic (no-model) profile used for the backtest's fit score."""
    profile, _ = validate_profile(extract_signals(pages), pages, fallback_company=fallback_company)
    return profile


def _d(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def backtest_rows(*, accounts: Iterable[Mapping], interactions: Iterable[Mapping], opportunities: Iterable[Mapping],
                  pages: Iterable[Mapping], as_of: date, until: date | None = None) -> dict[str, Any]:
    until = until or clock.today()
    if as_of >= until:
        raise ValueError("as_of must be before the simulation date")
    by_account: dict[str, list[Mapping]] = {}
    for r in interactions:
        by_account.setdefault(r["account_id"], []).append(r)
    pages_by: dict[str, list[dict]] = {}
    for p in pages:
        aid = p.get("company_account_id")
        if aid and _d(p["published_on"]) < as_of:
            pages_by.setdefault(aid, []).append(dict(p))

    scores: dict[str, tuple[int, int]] = {}
    for a in accounts:
        aid = a["account_id"]
        fit, _ = fit_score(profile_from_pages(pages_by.get(aid, [])))
        intent, _ = intent_from_rows(by_account.get(aid, []), [], [], as_of=as_of, use_pipeline=False)
        scores[aid] = (fit, intent)

    rows = []
    for o in opportunities:
        if o["stage"] not in CLOSED or not (as_of <= _d(o["close_date"]) <= until):
            continue
        if o["account_id"] not in scores:
            continue
        fit, intent = scores[o["account_id"]]
        rows.append({"opp_id": o["opp_id"], "account_id": o["account_id"], "won": o["stage"] == "closed_won",
                     "fit": fit, "intent": intent, "combined": combined(fit, intent), "tier": tier(fit, intent)})
    rows.sort(key=lambda r: r["opp_id"])

    def metric(key: str) -> float | None:
        return auc([r[key] for r in rows if r["won"]], [r[key] for r in rows if not r["won"]])

    tiers: dict[str, dict[str, Any]] = {}
    for t in ("high", "medium", "low"):
        sel = [r for r in rows if r["tier"] == t]
        won = sum(r["won"] for r in sel)
        tiers[t] = {"n": len(sel), "won": won, "win_rate": round(won / len(sel), 3) if sel else None}
    return {
        "as_of": as_of.isoformat(), "until": until.isoformat(), "metric": "roc_auc",
        "n": len(rows), "won": sum(r["won"] for r in rows), "lost": sum(not r["won"] for r in rows),
        "auc_intent": metric("intent"), "auc_fit": metric("fit"), "auc_combined": metric("combined"),
        "tiers": tiers, "rows": rows,
    }


def backtest(as_of: date) -> dict[str, Any]:
    """Owner job: score every account as of `as_of` from the database and the web corpus."""
    from corpai import audit
    from corpai.db import pg
    from corpai.sales.extract import load_web_pages

    with pg.connect_owner() as conn:
        accounts = conn.execute("SELECT account_id FROM crm.accounts ORDER BY account_id").fetchall()
        interactions = conn.execute(
            "SELECT account_id, occurred_on, sentiment, kind FROM crm.interactions WHERE occurred_on < %s",
            (as_of,)).fetchall()
        opportunities = conn.execute(
            "SELECT opp_id, account_id, stage, close_date FROM crm.opportunities WHERE stage IN ('closed_won', "
            "'closed_lost')").fetchall()
    report = backtest_rows(accounts=accounts, interactions=interactions, opportunities=opportunities,
                           pages=load_web_pages(), as_of=as_of)
    # Metadata only: the per-opportunity rows are CRM data (L3) and stay out of the audit log.
    audit.record_owner_job("sales_backtest", {
        "as_of": report["as_of"], "n": report["n"], "auc_intent": report["auc_intent"],
        "auc_fit": report["auc_fit"], "auc_combined": report["auc_combined"]})
    return report
