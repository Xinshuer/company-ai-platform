"""Read tools of hris, legal, code, finance, docs: visibility, labels, aggregates (§11, D7)."""

from __future__ import annotations

import importlib.util

import pytest

from corpai.policy import TOOL_CATALOGUE
from corpai.types import Level, Trust

pytestmark = [pytest.mark.anyio, pytest.mark.security]

NOT_VISIBLE = "not found or no permission"
HAS_RAG = importlib.util.find_spec("corpai.rag") is not None


async def test_every_catalogue_tool_is_registered():
    from corpai.mcp_servers import DOMAINS, build_server

    assert set(DOMAINS) == set(TOOL_CATALOGUE)
    for d in DOMAINS:
        tools = {t.name for t in await build_server(d).list_tools()}
        assert tools == set(TOOL_CATALOGUE[d]), d


async def test_no_tool_takes_a_user_id(seeded):
    """ENGINEERING security rule 1: identity comes from the token, never from arguments."""
    from corpai.mcp_servers import DOMAINS, build_server

    for d in DOMAINS:
        for t in await build_server(d).list_tools():
            props = set((t.input_schema or {}).get("properties", {}))
            assert not props & {"user_id", "principal", "requester_id", "approver_id", "owner_id", "level",
                                "clearance", "route"}, (d, t.name, props)


# ---- legal ----------------------------------------------------------------------------------

async def test_regulations_are_public_l1(call):
    env = await call("sales_staff", "qa", "legal", "search_regulations", query="GDPR")
    assert env.ok and env.data["count"] > 0
    assert env.level == Level.L1 and env.trust == Trust.REVIEWED


async def test_contracts_expiring_finds_planted_c0003_in_november(call):
    env = await call("legal_staff", "legal_review", "legal", "contracts_expiring", month="2026-11")
    assert env.ok
    ids = [c["contract_id"] for c in env.data["contracts"]]
    assert ids == ["C-0003"]
    c = env.data["contracts"][0]
    assert c["expires_on"] == "2026-11-30" and c["auto_renew"] is True and c["notice_days"] == 45
    assert c["notice_deadline"] == "2026-10-16"
    assert env.level == Level.L3


async def test_contracts_expiring_is_relative_to_the_simulation_date(call):
    soon = await call("legal_head", "legal_review", "legal", "contracts_expiring")  # default 30 days
    assert soon.data["today"] == "2026-10-01" and soon.data["to"] == "2026-10-31"
    assert "C-0003" not in [c["contract_id"] for c in soon.data["contracts"]]
    later = await call("legal_head", "legal_review", "legal", "contracts_expiring", within_days=60)
    assert "C-0003" in [c["contract_id"] for c in later.data["contracts"]]
    both = await call("legal_head", "legal_review", "legal", "contracts_expiring", within_days=10, month="2026-11")
    assert not both.ok and both.error == "bad_arguments"


async def test_ceo_sees_expiring_contracts_through_qa(call):
    env = await call("ceo", "qa", "legal", "contracts_expiring", month="2026-11")
    assert env.ok and [c["contract_id"] for c in env.data["contracts"]] == ["C-0003"]


async def test_canary_contract_is_l4_and_injected_contract_untrusted(call):
    canary = await call("legal_head", "legal_review", "legal", "get_contract", contract_id="C-0006")
    assert canary.ok and canary.level == Level.L4
    injected = await call("legal_staff", "legal_review", "legal", "get_contract", contract_id="C-0004")
    assert injected.ok and injected.trust == Trust.UNTRUSTED
    other_lawyer = await call("legal_staff", "legal_review", "legal", "get_contract", contract_id="C-0006")
    assert not other_lawyer.ok and other_lawyer.denied == [{"id": "legal.contract:C-0006", "reason": NOT_VISIBLE}]


async def test_lawyer_sees_only_own_contracts(call):
    env = await call("legal_staff", "legal_review", "legal", "search_contracts")
    assert env.ok and {c["handler_id"] for c in env.data["contracts"]} == {"u0031"}
    assert "text" not in env.data["contracts"][0]


# ---- hris -----------------------------------------------------------------------------------

def _counts(env) -> dict:
    return {r["dept"]: r["headcount"] for r in env.data["departments"]}


async def test_headcount_hides_small_groups_for_non_hr(call):
    env = await call("ceo", "qa", "hris", "headcount")
    assert env.ok
    counts = _counts(env)
    # small seed: exec 2, legal 4, it 3, hr 5, finance 6, sales 14, eng 32
    assert counts["exec"] == counts["legal"] == counts["it"] == "<5"
    assert counts["eng"] == 32 and counts["sales"] == 14 and counts["hr"] == 5
    assert env.level == Level.L2


async def test_hr_head_sees_every_department_with_small_groups_hidden(call):
    """One implementation (issues-log D-10): the head of HR gets every department from
    hr.headcount_by_dept(), with the same n<5 suppression as everybody else."""
    env = await call("hr_head", "hr_headcount", "hris", "headcount")
    assert env.ok and env.denied == []
    counts = _counts(env)
    assert set(counts) == {"exec", "sales", "eng", "legal", "finance", "hr", "it"}
    assert counts["legal"] == counts["exec"] == counts["it"] == "<5"
    assert counts["eng"] == 32 and counts["sales"] == 14  # current staff: leavers are not counted


async def test_hr_staff_get_explicit_denials_not_counts(call):
    # HR staff are not covered by the hr.aggregate rules: every department is denied, explicitly
    env = await call("hr_staff", "hr_headcount", "hris", "headcount")
    assert env.ok and env.data["departments"] == []
    assert {d["id"] for d in env.denied} == {f"hr.aggregate:headcount:{d}"
                                             for d in ("exec", "sales", "eng", "legal", "finance", "hr", "it")}


async def test_headcount_excludes_leavers(call):
    from tests.mcp.support import owner_rows

    left = owner_rows("SELECT dept, count(*) AS n FROM hr.employees WHERE left_on IS NOT NULL GROUP BY dept")
    assert {r["dept"] for r in left} >= {"eng", "sales"}  # synth plants leavers in big departments
    current = {r["dept"]: r["n"] for r in owner_rows(
        "SELECT dept, count(*) AS n FROM hr.employees WHERE left_on IS NULL GROUP BY dept")}
    env = await call("ceo", "qa", "hris", "headcount")
    counts = _counts(env)
    for dept, n in current.items():
        assert counts[dept] == (n if n >= 5 else "<5"), dept


async def test_other_heads_see_only_their_own_department(call):
    env = await call("sales_head", "report", "hris", "headcount")
    assert env.ok
    assert _counts(env) == {"sales": 14}
    denied = {d["id"] for d in env.denied}
    assert "hr.aggregate:headcount:eng" in denied and "hr.aggregate:headcount:legal" in denied
    legal = await call("legal_head", "report", "hris", "headcount")
    assert _counts(legal) == {"legal": "<5"}


async def test_staff_outside_hr_get_no_headcount(call):
    env = await call("sales_staff", "qa", "hris", "headcount")
    assert not env.ok and env.error == "tool_denied"


async def test_headcount_function_gives_staff_nothing_in_sql(personas):
    """Defence in depth: hr.headcount_by_dept() itself returns nothing to a non-head and only their
    own department to a head, whatever the Python says; ops.hr_headcount() is gone (D-10)."""
    from corpai.db import pg

    with pg.principal_tx_sync(personas["sales_staff"]) as conn:
        assert conn.execute("SELECT * FROM hr.headcount_by_dept()").fetchall() == []
    with pg.principal_tx_sync(personas["sales_head"]) as conn:
        rows = conn.execute("SELECT * FROM hr.headcount_by_dept()").fetchall()
        assert [r["dept"] for r in rows] == ["sales"]
        gone = conn.execute("SELECT to_regprocedure('ops.hr_headcount()') AS f").fetchone()["f"]
    assert gone is None


async def test_requisition_budget_only_where_visible(call):
    hr = await call("hr_head", "hr_headcount", "hris", "list_requisitions")
    assert hr.ok and all("budget" in r for r in hr.data["requisitions"])
    assert hr.level == Level.L3  # budgets are L3
    assert not any(d["id"].startswith("hr.requisition_budget:") for d in hr.denied)
    staff = await call("hr_staff", "hr_headcount", "hris", "list_requisitions")
    assert staff.ok and staff.data["count"] >= 6
    assert all(r["budget"] is None for r in staff.data["requisitions"])
    assert staff.level == Level.L2
    # explicit denial (rule 6): "no permission" is not silently "no budget"
    denied = {d["id"] for d in staff.denied}
    assert {f"hr.requisition_budget:{r['req_id']}" for r in staff.data["requisitions"]} <= denied


async def test_candidates_are_untrusted_and_panel_scoped(call):
    env = await call("hr_staff", "hr_screening", "hris", "get_candidate", candidate_id="CAND-001")
    assert env.ok and env.trust == Trust.UNTRUSTED and env.level >= Level.L3
    assert "resume_text" in env.data["candidate"]
    listing = await call("hr_staff", "hr_screening", "hris", "list_candidates", req_id="R-01")
    assert listing.ok and listing.data["count"] > 0
    assert all("resume_text" not in c for c in listing.data["candidates"])
    # R-04 belongs to another recruiter's panel
    other = await call("hr_staff", "hr_screening", "hris", "list_candidates", req_id="R-04")
    assert other.data["count"] == 0 and other.denied


async def test_get_employee_direct_manager_only(call):
    own_report = await call("sales_head", "hr_headcount", "hris", "get_employee", employee_id="u0011")
    assert own_report.ok and own_report.data["employee"]["employee_id"] == "u0011"
    assert "base_salary_eur" not in own_report.data["employee"]
    not_report = await call("sales_head", "hr_headcount", "hris", "get_employee", employee_id="u0021")
    assert not not_report.ok and not_report.denied[0]["reason"] == NOT_VISIBLE


# ---- code -----------------------------------------------------------------------------------

async def test_project_status_for_sales_and_tickets_for_members(call):
    sales = await call("sales_staff", "sales_profile", "code", "get_project_status")
    assert sales.ok and {p["project_id"] for p in sales.data["projects"]} == {"P-01", "P-02", "P-03"}
    assert "summary" not in sales.data["projects"][0]
    tickets = await call("eng_staff", "qa", "code", "search_tickets", limit=100)
    assert tickets.ok and {t["project_id"] for t in tickets.data["tickets"]} <= {"P-01", "P-02"}
    not_member = await call("eng_staff", "qa", "code", "search_tickets", project_id="P-03")
    assert not_member.ok and not_member.data["count"] == 0


async def test_sales_cannot_read_tickets(call):
    env = await call("sales_staff", "qa", "code", "search_tickets")
    assert not env.ok and env.error == "tool_denied"


# ---- finance --------------------------------------------------------------------------------

async def test_metric_tools_read_the_metric_layer(call, drafts, published):
    """Review fix: the tools call corpai.metrics as the caller (RLS + decide on each row)."""
    from tests.metrics.conftest import PUBLISHED_PERIOD

    env = await call("sales_staff", "qa", "finance", "list_metrics")
    assert env.ok and env.data["count"] > 0
    rev = next(m for m in env.data["metrics"] if m["metric_id"] == "revenue_recognised")
    assert rev["access"] == "visible" and {p["period"] for p in rev["periods"]} == {PUBLISHED_PERIOD}
    got = await call("sales_staff", "qa", "finance", "get_metric", metric_id="revenue_recognised",
                     period=PUBLISHED_PERIOD)
    assert got.ok and got.data["metric"]["status"] == "published" and got.data["metric"]["value"] is not None
    assert got.level == Level.L2 and not got.denied
    newest = await call("sales_staff", "qa", "finance", "get_metric", metric_id="revenue_recognised")
    assert newest.ok and newest.data["metric"]["period"] == PUBLISHED_PERIOD
    draft = await call("sales_staff", "qa", "finance", "get_metric", metric_id="revenue_recognised",
                       period="2026-09")
    assert draft.ok and draft.data["metric"]["status"] == "unavailable" and draft.data["metric"]["value"] is None
    assert draft.denied and draft.denied[0]["id"] == "fin.metric:revenue_recognised:2026-09"
    fin = await call("finance_staff", "qa", "finance", "get_metric", metric_id="revenue_recognised",
                     period="2026-09")
    assert fin.ok and fin.data["metric"]["status"] == "draft" and fin.level == Level.L3
    for bad in ({"metric_id": "nope"}, {"metric_id": "revenue_recognised", "period": "Q3"},
                {"metric_id": "revenue_recognised", "dims": {"colour": "red"}}):
        env = await call("sales_staff", "qa", "finance", "get_metric", **bad)
        assert not env.ok and env.error == "bad_arguments", bad


async def test_ledger_and_invoices_for_finance_only(call):
    env = await call("finance_staff", "report", "finance", "list_invoices", status="overdue")
    # finance_staff may not start the report agent (heads and the CEO only)
    assert not env.ok and env.error == "tool_denied"
    head = await call("finance_head", "report", "finance", "list_invoices", status="overdue")
    assert head.ok and head.data["count"] > 0 and head.level == Level.L3
    assert "A-0001" in {i["customer_account_id"] for i in head.data["invoices"]}  # planted overdue receivable
    sales = await call("sales_head", "report", "finance", "list_invoices")
    assert not sales.ok and sales.error == "tool_denied"


# ---- docs -----------------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_RAG, reason="corpai.rag (stream B1) not installed")
async def test_docs_search_respects_scope(call):
    env = await call("sales_staff", "qa", "docs", "search_docs", query="expense policy")
    assert env.ok
    assert all(h["collection"] in {"public", "general", "legal_regs", "sales"} for h in env.data["hits"])


@pytest.mark.skipif(HAS_RAG, reason="only meaningful while corpai.rag is missing")
async def test_docs_answer_unavailable_without_rag(call):
    env = await call("sales_staff", "qa", "docs", "search_docs", query="expense policy")
    assert not env.ok and env.error == "rag_unavailable"


@pytest.mark.skipif(not HAS_RAG, reason="corpai.rag (stream B1) not installed")
async def test_get_doc_by_scope_and_acl(call):
    regs = await call("sales_staff", "qa", "docs", "get_doc", doc_id="GDPR-Art-9")
    assert regs.ok and regs.level == Level.L1 and regs.data["doc"]["collection"] == "legal_regs"
    assert regs.data["doc"]["chunks"]
    handbook = await call("eng_staff", "qa", "docs", "get_doc", doc_id="gen-expense-policy")
    assert handbook.ok and handbook.level == Level.L2
    # another department's internal wiki: not visible (RLS), reported as a denial
    wiki = await call("sales_staff", "qa", "docs", "get_doc", doc_id="eng-wiki-oncall")
    assert not wiki.ok and wiki.denied == [{"id": "doc:eng-wiki-oncall", "reason": NOT_VISIBLE}]
    # a canary document is L4: hidden from L3 project members, labelled L4 for the CTO
    member = await call("eng_staff", "qa", "docs", "get_doc", doc_id="eng-P-01-readme")
    assert not member.ok
    cto = await call("eng_head", "qa", "docs", "get_doc", doc_id="eng-P-01-readme")
    assert cto.ok and cto.level == Level.L4


@pytest.mark.skipif(not HAS_RAG, reason="corpai.rag (stream B1) not installed")
async def test_get_doc_honours_the_agent_purpose(call):
    """A document the user may read by ACL but outside the agent's purpose is refused (rag_filter)."""
    env = await call("legal_head", "qa", "docs", "get_doc", doc_id="gen-expense-policy")
    assert env.ok  # qa covers general
    legal = await call("legal_head", "legal_review", "docs", "get_doc", doc_id="gen-expense-policy")
    # legal_review's purpose covers legal, legal_regs and playbooks only
    assert not legal.ok and legal.denied[0]["reason"] == "outside the document scope of this assistant"


@pytest.mark.skipif(not HAS_RAG, reason="corpai.rag (stream B1) not installed")
async def test_search_docs_labels_hits(call):
    env = await call("eng_staff", "qa", "docs", "search_docs", query="on-call rotation runbook")
    assert env.ok and env.data["count"] > 0
    assert env.level == max(h["level"] for h in env.data["hits"])
    assert all(s.startswith("rag.chunk:") for s in env.sources)
