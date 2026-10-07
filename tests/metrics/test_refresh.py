"""The refresh job: every metric's SQL runs on the seeded DB; suppression; versions; integrity."""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from corpai.metrics import apply_min_group, compute_all, load_metrics, refresh_snapshots, sign_off
from tests.metrics.conftest import owner_rows, run


@pytest.mark.parametrize("period", ["2026-09", "2026-Q3", "2025-10"])
def test_every_metric_sql_runs(seeded, period):
    out = compute_all(period)
    assert set(out) == set(load_metrics())
    for mid, rows in out.items():
        md = load_metrics()[mid]
        assert rows, f"{mid} returned no rows for {period}"
        for r in rows:
            assert set(r["dims"]) <= set(md.dims)
            assert r["value"] is None or isinstance(r["value"], Decimal)
            if r["suppressed"]:
                assert r["value"] is None
        if not md.dims or md.dims == ("tier",):
            continue
        assert any(not r["dims"] for r in rows), f"{mid} has no total row"


def test_refresh_writes_drafts_with_the_finance_label(drafts):
    rows = owner_rows("SELECT * FROM metrics.snapshots WHERE period = '2026-09'")
    assert drafts["2026-09"].rows == len(rows) > 0
    by_metric = {r["metric_id"] for r in rows}
    assert by_metric == set(load_metrics())
    defs = load_metrics()
    for r in rows:
        md = defs[r["metric_id"]]
        assert r["status"] == "draft" and r["owner_id"] == "svc:metrics"
        assert r["level"] == int(md.level) >= 2
        assert set(r["acl_groups"]) == set(md.draft_acl)
        assert set(r["acl_groups"]) <= {"dept:finance", "head:finance", "role:ceo"}
        assert r["published_level"] == 2 and r["published_acl"]
        assert r["sql_sha256"] == md.sql_sha256
        assert r["subject_ids"] == []


def test_min_group_suppression_never_stores_the_value(drafts):
    rows = owner_rows("SELECT dims_key, value, suppressed FROM metrics.snapshots "
                      "WHERE metric_id = 'headcount' AND period = '2026-09'")
    got = {r["dims_key"]: r for r in rows}
    for small in ("dept=legal", "dept=exec", "dept=it"):  # 4, 2 and 3 people
        assert got[small]["suppressed"] and got[small]["value"] is None
    assert got["dept=eng"]["value"] == 32 and not got["dept=eng"]["suppressed"]
    # opex carries payroll: a small department's spend is hidden too
    opex = {r["dims_key"]: r for r in owner_rows(
        "SELECT dims_key, value, suppressed FROM metrics.snapshots WHERE metric_id = 'opex_actual' "
        "AND period = '2026-09'")}
    assert opex["dept=exec"]["suppressed"] and opex["dept=exec"]["value"] is None
    assert opex["dept=sales"]["value"] is not None


def test_complementary_suppression():
    rows = [{"dims": {}, "value": Decimal(40), "n": 40}, {"dims": {"dept": "a"}, "value": Decimal(3), "n": 3},
            {"dims": {"dept": "b"}, "value": Decimal(30), "n": 30}, {"dims": {"dept": "c"}, "value": Decimal(7), "n": 7}]
    out = {tuple(r["dims"].values()): r for r in apply_min_group(rows, 5)}
    # a (3) is below 5; with the total shown, c (the smallest other group) must hide too
    assert out[("a",)]["suppressed"] and out[("c",)]["suppressed"]
    assert out[("c",)]["value"] is None and not out[("b",)]["suppressed"] and not out[()]["suppressed"]
    with pytest.raises(Exception):
        apply_min_group([{"dims": {}, "value": Decimal(1), "n": None}], 5)


def test_refresh_is_idempotent_and_drops_stale_drafts(seeded):
    period = "2026-05"
    first = refresh_snapshots(period)
    with psycopg.connect(_owner_dsn(), autocommit=True) as c:
        c.execute("INSERT INTO metrics.snapshots (metric_id, period, dims, dims_key, value, unit, sql_sha256, "
                  "published_level, published_acl, level, acl_groups) VALUES ('pipeline_value', %s, "
                  "'{\"stage\": \"bogus\"}', 'stage=bogus', 1, 'EUR', repeat('0', 64), 2, '{all}', 3, "
                  "'{dept:finance}')", (period,))
    second = refresh_snapshots(period)
    assert second.inserted == 0 and second.new_versions == 0 and second.rows == first.rows
    assert not owner_rows("SELECT 1 FROM metrics.snapshots WHERE period = %s AND dims_key = 'stage=bogus'",
                          (period,))


def _owner_dsn() -> str:
    from corpai.db import pg

    return pg.owner_dsn()


def test_signed_snapshots_are_frozen_and_changes_make_new_versions(seeded, personas):
    period = "2026-04"
    refresh_snapshots(period, metric_ids=["arr", "customers_active"])
    run(sign_off(personas["finance_head"], period, ["arr"]))
    with psycopg.connect(_owner_dsn(), autocommit=True) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE metrics.snapshots SET value = 1 WHERE metric_id = 'arr' AND period = %s", (period,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM metrics.snapshots WHERE metric_id = 'arr' AND period = %s", (period,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE metrics.snapshots SET status = 'draft' WHERE metric_id = 'arr' AND period = %s",
                      (period,))
    # The CRM changes after sign-off: the signed v1 stays, a new draft v2 appears for the CFO.
    with psycopg.connect(_owner_dsn(), autocommit=True) as c:
        c.execute("UPDATE crm.accounts SET arr_eur = arr_eur + 1000 WHERE account_id = 'A-0011'")
    try:
        rep = refresh_snapshots(period, metric_ids=["arr"])
    finally:
        with psycopg.connect(_owner_dsn(), autocommit=True) as c:
            c.execute("UPDATE crm.accounts SET arr_eur = arr_eur - 1000 WHERE account_id = 'A-0011'")
    assert rep.new_versions == 1
    rows = owner_rows("SELECT version, status, value FROM metrics.snapshots WHERE metric_id = 'arr' AND period = %s "
                      "ORDER BY version", (period,))
    assert [(r["version"], r["status"]) for r in rows] == [(1, "signed"), (2, "draft")]
    assert rows[1]["value"] - rows[0]["value"] == 1000
    # a draft is updated in place; a signed row whose result did not change is left alone
    assert refresh_snapshots(period, metric_ids=["customers_active"]).updated == 1
    run(sign_off(personas["finance_head"], period, ["customers_active"]))
    again = refresh_snapshots(period, metric_ids=["customers_active"])
    assert (again.unchanged, again.updated, again.new_versions) == (1, 0, 0)


def test_refresh_is_audited_as_an_owner_job(drafts):
    rows = owner_rows("SELECT actor, details FROM audit.log WHERE event = 'owner_job' AND actor = 'svc:metrics' "
                      "ORDER BY seq DESC")
    assert rows and any(r["details"].get("period") in ("2026-09", "2026-Q3") for r in rows)


def test_unknown_metric_or_grain_is_refused(seeded):
    with pytest.raises(KeyError):
        compute_all("2026-09", ["no_such_metric"])
    with pytest.raises(ValueError):
        refresh_snapshots("2026-9")
