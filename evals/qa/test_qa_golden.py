"""Q&A golden set (marker `eval`): the same questions asked by different people (golden.yaml).

Every case runs through the API (POST /api/runs, app qa) as each listed persona and checks the
citations: an expected document (or tool source) is cited, nothing in `never` is, and every cited
corpus document is one the persona may read by its stored ACL and level (read as the owner).

Fake model (default): every group must pass, every run. Live (`CORPAI_LLM=live python -m pytest
evals/qa -m eval`, eval paths only): retrieval and permissions are still code, the answer and its
citations come from the real model, so the gate is a hit rate of at least 0.8 over all groups and
zero leaks. CORPAI_EVAL_RUNS repeats each run.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path

import pytest
import yaml

from corpai.config import settings
from tests.api.support import login, owner_rows

pytestmark = [pytest.mark.eval, pytest.mark.anyio]

GOLDEN = yaml.safe_load(Path(__file__).with_name("golden.yaml").read_text(encoding="utf-8"))["questions"]
RUNS = max(1, int(os.getenv("CORPAI_EVAL_RUNS", "1")))
LIVE = settings().llm_mode == "live"
LIVE_MIN_HIT_RATE = 0.8
_results: dict[str, dict] = {}  # "<question id>/<persona>/<group>" -> {"hit": bool | None, "leaks": [...]}


def _readable(doc_id: str, principal) -> bool:
    rows = owner_rows("SELECT acl_groups, level FROM rag.documents WHERE doc_id = %s AND status = 'current'",
                      (doc_id,))
    if not rows:
        return True  # not a corpus document (a tool source): covered by the `never` patterns
    return int(rows[0]["level"]) <= int(principal.clearance) and bool(set(rows[0]["acl_groups"]) & principal.groups)


def check_group(group: dict, cited: list[str], principal) -> tuple[bool | None, list[str]]:
    """(expected citation found, or None when the group expects none; the leaks)."""
    hit = any(c in cited for c in group["cites"]) if group.get("cites") else None
    leaks = [c for c in cited for pat in group.get("never") or [] if fnmatch.fnmatchcase(c, pat)]
    leaks += [c for c in cited if not _readable(c, principal)]
    return hit, sorted(set(leaks))


def test_the_golden_set_is_big_enough_and_varied():
    groups = [g for q in GOLDEN for g in q["groups"]]
    assert len(GOLDEN) >= 30
    assert len({q["id"] for q in GOLDEN}) == len(GOLDEN)
    assert sum(1 for q in GOLDEN if len(q["groups"]) > 1) >= 10, "same question, different people"
    assert sum(1 for g in groups if g.get("never")) >= 12
    personas = {p for g in groups for p in g["personas"]}
    assert len(personas) >= 12


def test_the_leak_check_catches_forbidden_citations(personas):
    """The checker itself: a `never` pattern and, independently, the stored ACL both catch a citation
    the persona may not read; readable citations pass."""
    assert check_group({"never": ["sales-*"]}, ["sales-plan-A-0001", "pub-home"], personas["eng_staff"]) == \
        (None, ["sales-plan-A-0001"])
    # no `never` listed: the ACL check alone (C-0006 is handled by u0142, L4)
    assert check_group({"cites": ["C-0006"]}, ["C-0006"], personas["legal_staff"]) == (True, ["C-0006"])
    assert check_group({"cites": ["C-0006"]}, ["C-0006"], personas["legal_head"]) == (True, [])
    assert check_group({"cites": ["pub-home"]}, ["gen-travel-policy"], personas["auditor"]) == (False, [])


@pytest.mark.parametrize("case", GOLDEN, ids=[q["id"] for q in GOLDEN])
async def test_golden_question(case, app_client, personas):
    failures = []
    for gi, group in enumerate(case["groups"]):
        for key in group["personas"]:
            p = personas[key]
            await login(app_client, p.user_id)
            for n in range(RUNS):
                r = await app_client.post("/api/runs", json={"app": "qa", "input": case["question"]})
                assert r.status_code == 200, r.text
                run = r.json()
                assert run["status"] == "done", (case["id"], key, run["error"])
                cited = [c["doc_id"] for c in run["output"]["citations"]]
                hit, leaks = check_group(group, cited, p)
                answer = " ".join((run["output"]["text"] or "").split())[:200]
                _results[f"{case['id']}/{key}/{gi}/{n}"] = {
                    "hit": hit, "leaks": leaks, "cited": cited, "answer": answer,
                    "dropped": (run["output"].get("data") or {}).get("dropped_citations", 0)}
                if LIVE and not case["question"].isascii():  # does the answer keep the question's language?
                    print(f"\n{case['id']}/{key}: {answer}")
                if leaks:
                    failures.append(f"{key}: cited without permission {leaks}")
                if hit is False and not LIVE:
                    failures.append(f"{key}: none of {group['cites']} cited (got {cited})")
    assert not failures, f"{case['id']}: {failures}"


def test_hit_rate_and_leaks():
    """The summary gate (meaningful after the parametrised cases ran in this session)."""
    if len({k.split("/")[0] for k in _results}) < len(GOLDEN):
        pytest.skip("run the whole golden set first")
    graded = [v["hit"] for v in _results.values() if v["hit"] is not None]
    rate = sum(graded) / len(graded)
    leaks = [k for k, v in _results.items() if v["leaks"]]
    # printed for live runs (pytest -s): a miss is judged by a human from the citations and the answer
    print("\nqa golden:", json.dumps({
        "mode": settings().llm_mode, "runs": len(_results), "graded": len(graded), "hit_rate": round(rate, 3),
        "leaks": leaks, "dropped_citations": sum(v["dropped"] for v in _results.values()),
        "misses": {k: {"cited": v["cited"], "answer": v["answer"]} for k, v in _results.items() if v["hit"] is False},
    }, ensure_ascii=False))
    assert leaks == []
    assert rate >= (LIVE_MIN_HIT_RATE if LIVE else 1.0), f"hit rate {rate:.2f}"
