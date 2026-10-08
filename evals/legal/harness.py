"""M2 legal eval harness: run review_contract on the golden cases and check the results.

Same layering as Clause Check's evals:
  deterministic - verdict in the allowed set, the deciding playbook rule, required findings, overall
                  risk, quotes that exist verbatim, the trajectory (get_clause:<id> really called);
  statistical   - pass rate over N runs per case (CORPAI_EVAL_RUNS) and high-risk recall over the
                  must_flag / trap / injection / missing cases.

Fake LLM by default (deterministic, every case must pass); `CORPAI_LLM=live` runs the same cases
through the gateway against the real `local` route:

    CORPAI_LLM=live CORPAI_EVAL_RUNS=3 python -m pytest evals/legal -m eval -q

What the fake mode proves, and what it does not: the fake `legal.review` responder decides with
the same playbook regexes and injection detector the cases were written against, so a fake pass is
a *plumbing* test (prompting, tool loop, verdict mapping, quote verification, missing-term and
injection post-processing, storage). It says nothing about review quality with a real model. M2's
review counts as validated only after a live run (CORPAI_LLM=live, CORPAI_EVAL_RUNS>=3) meets the
gates in test_legal_eval.py. `test_lenient_model_is_caught_by_code` runs the high-risk cases with
a model that accepts everything, which shows the guarantees that hold in code whatever the model says.
"""

from __future__ import annotations

import json
from pathlib import Path

from corpai.legal import ReviewResult, review_contract
from corpai.types import Principal

CASES_PATH = Path(__file__).with_name("cases.json")
HEADER = "Agreement (eval case)\n\nBetween ABC Tech AB, Stockholm, Sweden and the Customer.\n\n"
HIGH_RISK_KINDS = frozenset({"must_flag", "trap", "injection", "missing"})


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def case_text(case: dict, contracts: dict[str, str]) -> str:
    return contracts[case["contract_id"]] if case.get("contract_id") else HEADER + case["text"]


def _why(c) -> str:
    """The reviewer's own reasoning, appended to a verdict failure: a live failure is judged by a
    human (sampling noise, an eval bug, or a real problem) and needs the rationale to do that."""
    return f" [{c.rule}; tools {c.tool_calls}; {' '.join(c.explanation.split())[:600]}]"


def check(case: dict, text: str, result: ReviewResult) -> list[str]:
    """Failure reasons (empty = pass)."""
    e = case["expect"]
    fails: list[str] = []
    by = {c.clause_id: c for c in result.clauses}
    for cid, want in e.get("clauses", {}).items():
        got = by.get(cid)
        if got is None:
            fails.append(f"clause {cid} missing from the result")
            continue
        if got.verdict not in want["verdict_in"]:
            fails.append(f"verdict {cid}: {got.verdict} not in {want['verdict_in']}{_why(got)}")
        if "rule" in want and (got.rule or "").split("@")[0] != want["rule"]:
            fails.append(f"rule {cid}: {got.rule} != {want['rule']}")
        for ref in want.get("must_consult", []):
            if f"get_clause:{ref}" not in got.tool_calls:
                fails.append(f"clause {cid} did not consult {ref} (calls: {got.tool_calls})")
    if "findings" in e and sorted(f.kind for f in result.findings) != sorted(e["findings"]):
        fails.append(f"findings {[f.kind for f in result.findings]} != {e['findings']} "
                     f"(clauses {[(f.clause_id, f.detected_by) for f in result.findings]})")
    if "finding_clause" in e and not any(f.clause_id == e["finding_clause"] for f in result.findings):
        fails.append(f"no finding attached to clause {e['finding_clause']}")
    for bad in e.get("no_verdicts", []):
        hit = [c for c in result.clauses if c.verdict == bad]
        if hit:
            fails.append(f"unexpected {bad} verdicts: {[c.clause_id for c in hit]}{_why(hit[0])}")
    if "overall_risk_in" in e and result.summary.get("overall_risk") not in e["overall_risk_in"]:
        fails.append(f"overall risk {result.summary.get('overall_risk')} not in {e['overall_risk_in']}")
    for c in result.clauses:
        for q in c.quotes:
            if text[q.start:q.end] != q.text:
                fails.append(f"quote of clause {c.clause_id} is not verbatim")
        if c.error:
            fails.append(f"clause {c.clause_id} error: {c.error}")
    if set(result.summary.get("routes", [])) - {"local"}:
        fails.append(f"routes {result.summary.get('routes')}")
    return fails


async def run_case(case: dict, principal: Principal, contracts: dict[str, str], run_id: str) -> list[str]:
    text = case_text(case, contracts)
    result = await review_contract(principal, text=text, contract_id=case.get("contract_id"), run_id=run_id,
                                   store=False)
    return check(case, text, result)


def high_risk_recall(results: dict[str, list[list[str]]], cases: list[dict]) -> float:
    """Share of high-risk runs whose verdict / finding assertions passed."""
    total = hit = 0
    for case in cases:
        if case["kind"] not in HIGH_RISK_KINDS or case["id"] not in results:
            continue
        for fails in results[case["id"]]:
            total += 1
            if not any(f.startswith(("verdict", "findings", "no finding")) for f in fails):
                hit += 1
    return hit / total if total else 1.0
