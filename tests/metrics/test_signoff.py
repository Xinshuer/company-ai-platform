"""Draft → signed → published: only the head of finance, in that order, audited, enforced in SQL too."""

from __future__ import annotations

from dataclasses import replace

import psycopg
import pytest

from corpai.db import pg
from corpai.metrics import get_metric, load_metrics, publish, refresh_snapshots, sign_off
from corpai.types import Level, Role
from tests.metrics.conftest import owner_rows

pytestmark = pytest.mark.security


_EVENT = {"metrics.sign": "metric_signed", "metrics.publish": "metric_published"}  # issues-log D-13


def _audit_since(seq: int, action: str) -> list[dict]:
    return owner_rows("SELECT actor, action, decision, resource, details FROM audit.log "
                      "WHERE seq > %s AND event = %s AND action = %s ORDER BY seq", (seq, _EVENT[action], action))


def _head_seq() -> int:
    return owner_rows("SELECT coalesce(max(seq), 0) AS s FROM audit.log")[0]["s"]


@pytest.mark.anyio
@pytest.mark.parametrize("who", ["finance_staff", "ceo", "eng_head", "hr_head", "platform_admin", "auditor"])
async def test_only_the_head_of_finance_signs(seeded, personas, who):
    period = "2026-03"
    refresh_snapshots(period, metric_ids=["revenue_recognised"])
    seq = _head_seq()
    with pytest.raises(PermissionError):
        await sign_off(personas[who], period, ["revenue_recognised"])
    with pytest.raises(PermissionError):
        await publish(personas[who], period, ["revenue_recognised"])
    denied = _audit_since(seq, "metrics.sign") + _audit_since(seq, "metrics.publish")
    assert [r["decision"] for r in denied] == ["deny", "deny"]
    assert all(r["actor"] == personas[who].user_id for r in denied)
    rows = owner_rows("SELECT status FROM metrics.snapshots WHERE metric_id = 'revenue_recognised' AND period = %s",
                      (period,))
    assert {r["status"] for r in rows} == {"draft"}


@pytest.mark.anyio
async def test_sign_then_publish(seeded, personas):
    period = "2026-02"
    ids = ["revenue_recognised", "opex_actual", "headcount", "cash_net_flow"]
    refresh_snapshots(period, metric_ids=ids)
    cfo = personas["finance_head"]
    seq = _head_seq()
    early = await publish(cfo, period, ids)  # nothing signed yet
    assert early.rows == 0 and set(early.skipped) == set(ids)
    signed = await sign_off(cfo, period, ids)
    assert set(signed.changed) == set(ids) and signed.rows > 0
    again = await sign_off(cfo, period, ids)  # already signed: nothing to do
    assert again.rows == 0
    # signed is not published: a sales rep still sees nothing
    assert (await get_metric(personas["sales_staff"], "revenue_recognised", period)).status == "unavailable"
    pub = await publish(cfo, period, ids)
    assert pub.rows == signed.rows
    mv = await get_metric(personas["sales_staff"], "revenue_recognised", period)
    assert mv.status == "published" and mv.level == Level.L2
    defs = load_metrics()
    for r in owner_rows("SELECT * FROM metrics.snapshots WHERE period = %s", (period,)):
        assert r["status"] == "published" and r["signed_by"] == cfo.user_id and r["published_by"] == cfo.user_id
        assert r["level"] == 2 and r["acl_groups"] == r["published_acl"]
        dept = r["dept"] if "dept" in defs[r["metric_id"]].dims else defs[r["metric_id"]].dept
        assert list(defs[r["metric_id"]].published_acl(dept)) == r["acl_groups"]
    allowed = [r for r in _audit_since(seq, "metrics.sign") + _audit_since(seq, "metrics.publish")
               if r["decision"] == "allow"]
    assert len(allowed) == 4 and all(r["resource"] == f"fin.metric:{period}" for r in allowed)


def _forged_call(sets: dict[str, str], fn: str = "sign") -> None:
    """Call the SQL function as corpai_app with hand-set session variables (no Python checks)."""
    with psycopg.connect(pg.app_dsn()) as conn:
        for k, v in sets.items():
            conn.execute("SELECT set_config(%s, %s, true)", (k, v))
        conn.execute(f"SELECT * FROM metrics.{fn}(%s, %s)", ("2026-01", ["revenue_recognised"]))


@pytest.mark.parametrize("fn", ["sign", "publish"])
@pytest.mark.parametrize("sets", [
    {},  # no principal at all
    {"app.user_id": "u0041", "app.role": "manager", "app.dept": "finance", "app.groups": "dept:finance,all",
     "app.clearance": "4"},                                                  # forged role, no head:finance group
    {"app.user_id": "u0001", "app.role": "ceo", "app.dept": "exec", "app.groups": "role:ceo,head:exec,all",
     "app.clearance": "4"},                                                  # the CEO
    {"app.user_id": "u0040", "app.role": "manager", "app.dept": "finance", "app.groups": "",
     "app.clearance": "0"},                                                  # deactivated CFO
    {"app.user_id": "u0010", "app.role": "manager", "app.dept": "sales",
     "app.groups": "head:sales,head:finance,all", "app.clearance": "4"},    # wrong dept
])
def test_sql_functions_refuse_everyone_but_the_finance_head(seeded, sets, fn):
    refresh_snapshots("2026-01", metric_ids=["revenue_recognised"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _forged_call(sets, fn)


def test_app_role_cannot_write_snapshots(seeded, personas):
    with pg.principal_tx_sync(personas["finance_head"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE metrics.snapshots SET status = 'published'")


@pytest.mark.anyio
async def test_deactivated_cfo_cannot_sign(seeded, personas):
    with pytest.raises(PermissionError):
        await sign_off(replace(personas["finance_head"], active=False), "2026-01", ["revenue_recognised"])
    with pytest.raises(PermissionError):
        await sign_off(replace(personas["finance_head"], role=Role.EMPLOYEE), "2026-01", ["revenue_recognised"])
