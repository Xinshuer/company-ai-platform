"""D7: no metric exposes a per-person value, in definitions or in the stored snapshots."""

from __future__ import annotations

import re

import pytest

from corpai.metrics import compute_all, load_metrics
from corpai.metrics.definitions import ALLOWED_DIMS
from tests.metrics.conftest import owner_rows

pytestmark = pytest.mark.security


def test_snapshot_dims_are_aggregates_only(drafts, published):
    person_ids = {r["user_id"] for r in owner_rows("SELECT user_id FROM core.users")}
    person_ids |= {r["candidate_id"] for r in owner_rows("SELECT candidate_id FROM hr.candidates")}
    person_ids |= {r["contact_id"] for r in owner_rows("SELECT contact_id FROM crm.contacts")}
    names = {r["name"] for r in owner_rows("SELECT name FROM core.users UNION SELECT name FROM crm.contacts")}
    rows = owner_rows("SELECT metric_id, dims, subject_ids FROM metrics.snapshots")
    assert rows
    for r in rows:
        assert set(r["dims"]) <= ALLOWED_DIMS, r
        assert r["subject_ids"] == []
        for v in r["dims"].values():
            assert v not in person_ids and v not in names and not re.fullmatch(r"u\d{4}", v), r


@pytest.mark.parametrize("period", ["2026-09", "2026-Q3"])
def test_people_derived_groups_below_five_are_never_stored(drafts, period):
    computed = compute_all(period)
    stored = {(r["metric_id"], r["dims_key"]): r for r in owner_rows(
        "SELECT DISTINCT ON (metric_id, dims_key) metric_id, dims_key, value, suppressed FROM metrics.snapshots "
        "WHERE period = %s ORDER BY metric_id, dims_key, version DESC", (period,))}
    checked = 0
    for mid, rows in computed.items():
        md = load_metrics()[mid]
        if md.min_group <= 1:
            continue
        for r in rows:
            key = "&".join(f"{k}={r['dims'][k]}" for k in sorted(r["dims"]))
            s = stored[(mid, key)]
            if r["n"] is not None and int(r["n"]) < md.min_group:
                assert s["suppressed"] and s["value"] is None, (mid, key)
                checked += 1
    assert checked >= 9  # legal, exec and it for headcount, attrition and the three opex metrics


def test_no_metric_reads_pay_or_ratings():
    for md in load_metrics().values():
        assert not {"hr.compensation", "hr.performance"} & set(md.sources)
        assert "compensation" not in md.sql and "performance" not in md.sql
