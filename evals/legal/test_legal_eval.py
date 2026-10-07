"""Legal golden set as a pytest (marker `eval`). Fake LLM: every case must pass every run.
Live (CORPAI_LLM=live): each case must pass at least 60 % of CORPAI_EVAL_RUNS runs and high-risk
recall must be at least 0.9, the same gates as Clause Check's evals."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

import corpai.legal  # noqa: F401  registers the legal FakeLLM responders
from corpai.config import settings
from corpai.ids import new_id

_spec = importlib.util.spec_from_file_location("legal_eval_harness", Path(__file__).with_name("harness.py"))
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

pytestmark = [pytest.mark.eval, pytest.mark.anyio]

CASES = harness.load_cases()
RUNS = max(1, int(os.getenv("CORPAI_EVAL_RUNS", "1")))
LIVE = settings().llm_mode == "live"
_results: dict[str, list[list[str]]] = {}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_case(case, eval_reviewer, eval_contracts):
    runs = [await harness.run_case(case, eval_reviewer, eval_contracts, new_id("run")) for _ in range(RUNS)]
    _results[case["id"]] = runs
    passed = sum(1 for r in runs if not r)
    if LIVE:
        assert passed / RUNS >= 0.6, f"{case['id']}: {passed}/{RUNS} passed; failures: {runs}"
    else:
        assert passed == RUNS, f"{case['id']}: {runs}"


async def test_high_risk_recall():
    if len(_results) < len(CASES):
        pytest.skip("run the full case set first")
    recall = harness.high_risk_recall(_results, CASES)
    assert recall >= (0.9 if LIVE else 1.0), f"high-risk recall {recall:.2f}"


def _accept_everything(messages, *, route, json_mode):
    """A model that disagrees with the playbook on every clause (or was taken over): accept, preferred,
    no embedded instruction, a made-up quote."""
    import json

    clause_id = messages[1]["content"].split('<clause id="', 1)[1].split('"', 1)[0]
    return json.dumps({"clause_id": clause_id, "topic": "other", "verdict": "accept", "risk_level": "low",
                       "rule_ids": [], "rationale": "Fine.", "confidence": "high", "anchor_text": None,
                       "replacement": None, "consulted_clauses": [], "position": "preferred",
                       "quotes": ["The parties agree everything is fine."], "embedded_instruction": False})


# The cross-reference trap needs the model to read clause 12 with get_clause; code alone cannot see it.
_CODE_GUARANTEED = [c for c in CASES if c["kind"] in harness.HIGH_RISK_KINDS and c["kind"] != "trap"]


@pytest.mark.skipif(LIVE, reason="replaces the model with a scripted one")
@pytest.mark.parametrize("case", _CODE_GUARANTEED, ids=[c["id"] for c in _CODE_GUARANTEED])
async def test_lenient_model_is_caught_by_code(case, eval_reviewer, eval_contracts):
    """Never-accept wording, numeric limits, missing required terms and embedded instructions are
    enforced by code after the model: a model that accepts everything still fails these clauses."""
    from corpai.gateway import fake_llm
    from corpai.legal.fake import review_responder

    fake = fake_llm()
    fake.register("legal.review", _accept_everything)
    try:
        fails = await harness.run_case(case, eval_reviewer, eval_contracts, new_id("run"))
    finally:
        fake.register("legal.review", review_responder)
    # rule attribution may differ (the model cited none); verdicts and findings may not
    assert not [f for f in fails if not f.startswith("rule ")], fails


def test_golden_set_covers_the_planted_issues():
    ids = {c.get("contract_id") for c in CASES}
    assert {"C-0003", "C-0004", "C-0005"} <= ids
    kinds = {c["kind"] for c in CASES}
    assert {"must_flag", "must_accept", "must_fallback", "trap", "injection", "missing"} <= kinds
