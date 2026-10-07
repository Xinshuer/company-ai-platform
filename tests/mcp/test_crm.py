"""crm tools: row visibility per persona, denied entries, labels (architecture §11, design-review 五)."""

from __future__ import annotations

import pytest

from corpai.types import Level, Trust

pytestmark = [pytest.mark.anyio, pytest.mark.security]

NOT_VISIBLE = "not found or no permission"


async def test_sales_staff_reads_own_account(call):
    env = await call("sales_staff", "sales_profile", "crm", "get_account", account_id="A-0002")
    assert env.ok, env
    acc = env.data["account"]
    assert acc["account_id"] == "A-0002" and acc["owner_id"] == "u0011"
    assert {c["contact_id"] for c in acc["contacts"]} >= {"CT-00004", "CT-00005"}
    assert env.level == Level.L3
    assert env.label["dept"] == "sales" and env.trust == Trust.INTERNAL
    assert "crm.account:A-0002" in env.sources
    assert env.denied == []
    assert "acl_groups" not in acc and "level" not in acc  # label columns stay out of the data


async def test_other_reps_account_is_a_denied_entry(call):
    env = await call("sales_staff", "sales_profile", "crm", "get_account", account_id="A-0001")
    assert not env.ok and env.error == "not_found_or_denied"
    assert env.data is None
    assert env.denied == [{"id": "crm.account:A-0001", "reason": NOT_VISIBLE}]


async def test_unknown_account_looks_exactly_like_a_forbidden_one(call):
    forbidden = await call("sales_staff", "sales_profile", "crm", "get_account", account_id="A-0001")
    missing = await call("sales_staff", "sales_profile", "crm", "get_account", account_id="A-9999")
    assert forbidden.denied[0]["reason"] == missing.denied[0]["reason"] == NOT_VISIBLE
    assert forbidden.error == missing.error


async def test_search_returns_only_own_accounts(call):
    env = await call("sales_staff", "sales_profile", "crm", "search_accounts")
    ids = {a["account_id"] for a in env.data["accounts"]}
    assert ids == {"A-0002", "A-0004", "A-0006"}
    head = await call("sales_head", "sales_profile", "crm", "search_accounts")
    assert len(head.data["accounts"]) == 12


async def test_search_query_is_data_not_sql(call):
    env = await call("sales_staff", "sales_profile", "crm", "search_accounts", query="%' OR 1=1 --")
    assert env.ok and env.data["accounts"] == []
    env = await call("sales_staff", "sales_profile", "crm", "search_accounts", query="%")
    assert env.ok and env.data["accounts"] == []  # '%' is matched literally, not as a wildcard


async def test_eng_staff_has_no_crm(call):
    env = await call("eng_staff", "qa", "crm", "search_accounts")
    assert not env.ok and env.error == "tool_denied"
    assert env.data is None and env.denied and env.denied[0]["id"] == "crm.search_accounts"


async def test_legal_with_time_boxed_grant_sees_exactly_that_account(call):
    env = await call("legal_staff", "legal_review", "crm", "get_account", account_id="A-0003")
    assert env.ok and env.data["account"]["account_id"] == "A-0003"
    other = await call("legal_staff", "legal_review", "crm", "get_account", account_id="A-0002")
    assert not other.ok and other.denied[0]["reason"] == NOT_VISIBLE


async def test_canary_interaction_raises_the_envelope_to_l4(call):
    env = await call("sales_head", "sales_profile", "crm", "get_interactions", account_id="A-0001", limit=100)
    assert env.ok
    assert "I-00001" in {i["interaction_id"] for i in env.data["interactions"]}
    assert env.level == Level.L4
    # the owning rep (u0100, clearance L3) sees the account's interactions but never the L4 canary row
    from corpai.identity import directory

    rep = await directory.get_principal("u0100")
    own = await call(rep, "sales_profile", "crm", "get_interactions", account_id="A-0001", limit=100)
    assert own.ok and own.data["count"] > 0
    assert "I-00001" not in {i["interaction_id"] for i in own.data["interactions"]}
    assert own.level == Level.L3
    other = await call("sales_staff", "sales_profile", "crm", "get_interactions", account_id="A-0001")
    assert not other.ok and other.denied[0]["reason"] == NOT_VISIBLE


async def test_opportunities_filtered_by_rls(call):
    env = await call("sales_staff", "sales_profile", "crm", "list_opportunities")
    assert env.ok and env.data["count"] > 0
    assert {o["account_id"] for o in env.data["opportunities"]} <= {"A-0002", "A-0004", "A-0006"}
    denied = await call("sales_staff", "sales_profile", "crm", "list_opportunities", account_id="A-0001")
    assert not denied.ok and denied.denied[0]["id"] == "crm.account:A-0001"


async def test_bad_id_is_refused_before_sql(call):
    env = await call("sales_staff", "sales_profile", "crm", "get_account", account_id="A-0002' OR '1'='1")
    assert not env.ok and env.error == "bad_arguments"
    assert "OR" not in (env.message or "")  # the message never echoes the argument
