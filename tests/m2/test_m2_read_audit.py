"""The legal pages read contracts and saved reviews directly (RLS), not through MCP tools, so each read
writes its own metadata-only `retrieval` audit row (issues-log WR-11): who read which review, at what
level, and a probe for someone else's review is on record too."""

from __future__ import annotations

import json

import pytest

from tests.api.support import owner_rows
from tests.m2.conftest import legal_run

pytestmark = pytest.mark.anyio


def _last_seq() -> int:
    return int(owner_rows("SELECT coalesce(max(seq), 0) AS s FROM audit.log")[0]["s"])


def _reads(actor: str, since: int) -> list[dict]:
    return owner_rows("SELECT action, resource, decision, reason, level, details FROM audit.log WHERE seq > %s"
                      " AND actor = %s AND event = 'retrieval' AND action LIKE 'legal.%%' ORDER BY seq",
                      (since, actor))


async def test_contract_list_and_saved_review_reads_are_audited(app_client, as_persona, fake_llm, contracts):
    staff = await as_persona("legal_staff")
    run = await legal_run(app_client, {"mode": "review", "contract_id": "C-0005"})
    rv = run["output"]["data"]["review"]
    before = _last_seq()

    listed = (await app_client.get("/api/legal/contracts")).json()["contracts"]
    reviews = (await app_client.get("/api/legal/reviews?contract_id=C-0005")).json()["reviews"]
    full = await app_client.get(f"/api/legal/reviews/{rv['review_id']}")
    assert full.status_code == 200

    rows = _reads(staff.user_id, before)
    assert [(r["action"], r["decision"]) for r in rows] == [
        ("legal.list_contracts", "allow"), ("legal.list_reviews", "allow"), ("legal.get_review", "allow")]
    c_row, l_row, g_row = rows
    assert c_row["details"]["contract_ids"] == [c["contract_id"] for c in listed]
    assert c_row["level"] == max(c["level"] for c in listed)
    assert l_row["resource"] == "legal.contract:C-0005" and rv["review_id"] in l_row["details"]["review_ids"]
    assert l_row["details"]["count"] == len(reviews)
    assert g_row["resource"] == f"legal.review:{rv['review_id']}" and g_row["level"] == rv["level"]
    assert g_row["details"] == {"contract_id": "C-0005", "run_id": rv["run_id"]}
    # metadata only: no contract or review text in the trail
    blob = json.dumps([r["details"] for r in rows])
    assert contracts["C-0005"]["text"][:60] not in blob
    for c in rv["clauses"]:
        for q in c["quotes"]:
            assert q["text"] not in blob


async def test_reading_a_review_you_may_not_see_is_audited_as_a_denial(app_client, as_persona):
    staff = await as_persona("legal_staff")
    before = _last_seq()
    r = await app_client.get("/api/legal/reviews/rev_01abcdefghjkmnpqrstv")
    assert r.status_code == 404
    (row,) = _reads(staff.user_id, before)
    assert (row["action"], row["resource"], row["decision"], row["reason"]) == \
        ("legal.get_review", "legal.review:rev_01abcdefghjkmnpqrstv", "deny", "not_found_or_not_visible")
