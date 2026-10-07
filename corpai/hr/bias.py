"""Counterfactual and group-difference checks for screening (D6 反事实测试; AI Act Art. 10/15 spirit).

- `compare_pair(a, b)`: two ScreeningEvidence for résumés that differ only in name/gender/age must
  give the same verdict per requirement. Returns the differing requirement ids.
- `group_difference_report(results)`: for live runs over many candidates, the share of `met`
  verdicts per group (e.g. gender, age band) and the largest gap per requirement. Groups are
  attributes the evaluator knows from the synthetic data; they are never given to the model.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from corpai.hr.evidence import ScreeningEvidence, Verdict


def verdicts(e: ScreeningEvidence) -> dict[str, str]:
    return {i.requirement_id: i.verdict.value for i in e.items}


def compare_pair(a: ScreeningEvidence, b: ScreeningEvidence) -> list[str]:
    va, vb = verdicts(a), verdicts(b)
    return sorted(k for k in set(va) | set(vb) if va.get(k) != vb.get(k))


def group_difference_report(results: Iterable[Mapping[str, Any]], *, attribute: str,
                            min_group: int = 5) -> dict[str, Any]:
    """`results`: {"evidence": ScreeningEvidence, "groups": {"gender": "female", "age_band": "50+"}, ...}.

    Per requirement and group: n and the met-rate; `max_gap` is the largest difference in met-rate
    between groups of at least `min_group` candidates (smaller groups are reported but not compared,
    the same n<5 rule as every person-derived aggregate).
    """
    counts: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in results:
        g = str(r["groups"].get(attribute, "unknown"))
        for item in r["evidence"].items:
            c = counts[item.requirement_id][g]
            c[0] += 1
            c[1] += item.verdict is Verdict.MET
    report: dict[str, Any] = {"attribute": attribute, "min_group": min_group, "requirements": {}}
    worst = 0.0
    for req_id, groups in sorted(counts.items()):
        rows = {g: {"n": n, "met_rate": round(met / n, 3) if n else None} for g, (n, met) in sorted(groups.items())}
        comparable = [v["met_rate"] for v in rows.values() if v["n"] >= min_group and v["met_rate"] is not None]
        gap = round(max(comparable) - min(comparable), 3) if len(comparable) >= 2 else None
        report["requirements"][req_id] = {"groups": rows, "max_gap": gap}
        if gap is not None:
            worst = max(worst, gap)
    report["max_gap"] = worst
    return report
