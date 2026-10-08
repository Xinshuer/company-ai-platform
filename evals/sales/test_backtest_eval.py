"""Backtest eval of the sales scores (design-review 二.13: 销售意向看准确率和混淆矩阵; 七 M4: 回测).

Metric: ROC AUC = the probability that a randomly chosen won opportunity scored higher than a
randomly chosen lost one (0.5 = no signal, 1.0 = perfect ranking), plus the win rate per tier.
Scores use only data before the cut-off (web pages published before it, CRM interactions before
it; no opportunity stages or quotes, which carry no history and would leak the outcome).

Data: the full-size synthetic company for seeds 1-20 (pooled; a single small world has only a handful
of closed deals), cut-off 2026-04-01, outcomes closed between the cut-off and the simulation date.
Pure computation on in-memory worlds: no database, no model calls.

What the synthetic data supports (measured 2026-10-08, seeds 1-20 pooled, n=257):
intent AUC 0.67, combined 0.66, fit ≈ 0.57; win rate high > medium > low.

Read those numbers with the baseline next to them. In the generator (corpai/synth/crm.py) prospects
can only lose and existing customers win about 3:1, and interactions are weighted by status. The
trivial score "is the account a customer" reaches AUC 0.69 on the same rows, so the pooled intent AUC
and the pooled tier ordering mostly reflect account status, not buying intent. Within customer
accounts only (179 won, 48 lost) intent AUC is 0.57 and combined 0.59: a weak signal above chance, and
the tiers are not ordered there (high 0.83, medium 0.77, low 0.78). The eval therefore reports the
status baseline and the within-customer AUC, and asserts the within-customer AUC, not just the pooled
one. Firmographics were not generated to predict outcomes, so fit is reported but not asserted. A
generator that drew outcomes from interaction recency or sentiment, independent of status, would be
needed before claiming that intent predicts buying.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from corpai.sales import auc, backtest_rows
from corpai.synth import generate

pytestmark = pytest.mark.eval

SEEDS = range(1, 21)
AS_OF = date(2026, 4, 1)
UNTIL = date(2026, 10, 1)


@pytest.fixture(scope="module")
def pooled() -> dict:
    rows, per_seed = [], []
    for seed in SEEDS:
        w = generate(seed, small=False)
        status = {a["account_id"]: a["status"] for a in w.rows("crm.accounts")}
        r = backtest_rows(accounts=w.rows("crm.accounts"), interactions=w.rows("crm.interactions"),
                          opportunities=w.rows("crm.opportunities"), pages=w.web_pages, as_of=AS_OF, until=UNTIL)
        rows += [{**x, "is_customer": int(status.get(x["account_id"]) == "customer")} for x in r["rows"]]
        per_seed.append({"seed": seed, "n": r["n"], "auc_intent": r["auc_intent"]})
    won = [x for x in rows if x["won"]]
    lost = [x for x in rows if not x["won"]]
    cust_won = [x for x in won if x["is_customer"]]
    cust_lost = [x for x in lost if x["is_customer"]]

    def _tiers(sel_rows: list[dict]) -> dict:
        out = {}
        for t in ("high", "medium", "low"):
            sel = [x for x in sel_rows if x["tier"] == t]
            out[t] = {"n": len(sel), "win_rate": round(sum(x["won"] for x in sel) / len(sel), 3) if sel else None}
        return out

    report = {"as_of": AS_OF.isoformat(), "seeds": [SEEDS.start, SEEDS.stop - 1], "n": len(rows), "won": len(won),
              "lost": len(lost), **{f"auc_{k}": auc([x[k] for x in won], [x[k] for x in lost])
                                    for k in ("intent", "fit", "combined")},
              # the trivial baseline: "is the account an existing customer"
              "auc_is_customer": auc([x["is_customer"] for x in won], [x["is_customer"] for x in lost]),
              "customers": {"won": len(cust_won), "lost": len(cust_lost),
                            **{f"auc_{k}": auc([x[k] for x in cust_won], [x[k] for x in cust_lost])
                               for k in ("intent", "fit", "combined")},
                            "tiers": _tiers([x for x in rows if x["is_customer"]])},
              "tiers": _tiers(rows), "per_seed": per_seed}
    print("\nsales backtest:", json.dumps({k: v for k, v in report.items() if k != "per_seed"}))
    return report


def test_enough_outcomes(pooled):
    assert pooled["n"] >= 150 and pooled["won"] >= 50 and pooled["lost"] >= 30


def test_intent_separates_won_from_lost(pooled):
    # pooled: mostly account status (see the module docstring and test_status_baseline_is_reported)
    assert pooled["auc_intent"] >= 0.62


def test_intent_signal_within_customer_accounts(pooled):
    """The score must beat chance where status cannot explain the outcome (customers only)."""
    c = pooled["customers"]
    assert c["won"] >= 50 and c["lost"] >= 30
    assert c["auc_intent"] >= 0.55 and c["auc_combined"] >= 0.55


def test_status_baseline_is_reported(pooled):
    """The pooled intent AUC does not beat "is the account a customer"; the report must show both so
    nobody reads the pooled figure as buying intent."""
    assert pooled["auc_is_customer"] is not None and pooled["customers"]["auc_intent"] is not None
    assert pooled["customers"]["auc_intent"] < pooled["auc_intent"]


def test_combined_score_beats_chance(pooled):
    assert pooled["auc_combined"] >= 0.58


def test_tiers_are_ordered_by_win_rate(pooled):
    # Pooled over all accounts (status-driven); within customers the tiers are not ordered (docstring).
    t = pooled["tiers"]
    assert all(t[k]["n"] >= 20 for k in t)
    assert t["high"]["win_rate"] > t["medium"]["win_rate"] > t["low"]["win_rate"]


def test_backtest_uses_no_data_after_the_cut_off():
    w = generate(7, small=False)
    before = [i for i in w.rows("crm.interactions") if i["occurred_on"] < AS_OF]
    full = backtest_rows(accounts=w.rows("crm.accounts"), interactions=w.rows("crm.interactions"),
                         opportunities=w.rows("crm.opportunities"), pages=w.web_pages, as_of=AS_OF, until=UNTIL)
    cut = backtest_rows(accounts=w.rows("crm.accounts"), interactions=before,
                        opportunities=w.rows("crm.opportunities"),
                        pages=[p for p in w.web_pages if p["published_on"] < AS_OF.isoformat()],
                        as_of=AS_OF, until=UNTIL)
    assert full["rows"] == cut["rows"]
