"""RLS backstop on rag.chunks / rag.documents: even a query with no WHERE clause, run as the app role
inside principal_tx, sees exactly the rows core.can_read allows - so a retrieval path that forgot the
policy filter (a debug endpoint, a re-ranker, a cache) still cannot leak."""

from __future__ import annotations

from dataclasses import replace

import pytest

from corpai.db import pg as pgmod
from corpai.synth.personas import PERSONAS
from corpai.types import Principal
from tests.rag.helpers import owner_rows

pytestmark = pytest.mark.security


def _may_read(p: Principal, level: int, acl: list[str]) -> bool:
    return p.active and level <= int(p.clearance) and bool(set(acl) & set(p.groups))


@pytest.mark.parametrize("table", ["rag.chunks", "rag.documents"])
@pytest.mark.parametrize("who", sorted(PERSONAS))
def test_raw_select_without_filter_sees_exactly_the_readable_rows(personas, who, table):
    p = personas[who]
    key = "chunk_id" if table == "rag.chunks" else "doc_id || '@' || version"
    everything = owner_rows(f"SELECT {key} AS k, level, acl_groups FROM {table}")
    expected = {r["k"] for r in everything if _may_read(p, r["level"], r["acl_groups"])}
    with pgmod.principal_tx_sync(p) as conn:
        seen = {r["k"] for r in conn.execute(f"SELECT {key} AS k FROM {table}").fetchall()}
    assert seen == expected
    assert len(seen) < len(everything), "every persona has something it cannot read"


def test_sales_staff_cannot_see_finance_hr_exec_or_other_reps_plans_even_unfiltered(personas):
    with pgmod.principal_tx_sync(personas["sales_staff"]) as conn:
        rows = conn.execute("SELECT DISTINCT collection, doc_id FROM rag.chunks").fetchall()
    collections = {r["collection"] for r in rows}
    assert not collections & {"finance", "hr", "exec"}
    assert "sales-plan-A-0001" not in {r["doc_id"] for r in rows}
    assert "sales-plan-A-0002" in {r["doc_id"] for r in rows}


def test_app_role_without_principal_sees_nothing(seeded):
    with pgmod.connect_app() as conn:
        assert conn.execute("SELECT count(*) AS n FROM rag.chunks").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM rag.documents").fetchone()["n"] == 0


def test_inactive_principal_sees_nothing_unfiltered(personas):
    gone = replace(personas["ceo"], active=False)
    with pgmod.principal_tx_sync(gone) as conn:
        assert conn.execute("SELECT count(*) AS n FROM rag.chunks").fetchone()["n"] == 0


def test_canary_and_l4_rows_need_clearance_and_acl(personas):
    # eng_staff is L3: the L4 P-01 README (canary) is invisible even to a P-01 member
    staff = personas["eng_staff"]
    with pgmod.principal_tx_sync(staff) as conn:
        assert conn.execute("SELECT count(*) AS n FROM rag.chunks WHERE level = 4").fetchone()["n"] == 0
    with pgmod.principal_tx_sync(personas["eng_head"]) as conn:
        assert conn.execute("SELECT count(*) AS n FROM rag.chunks WHERE doc_id = 'eng-P-01-readme'"
                            ).fetchone()["n"] > 0
