"""Retrieval: the policy filter in SQL, RLS underneath, hybrid ranking, level aggregation, audit.

The design-review test (二.5 / 五): the same question asked by different people returns doc ids that
match each person's rights; another department's collection never shows up.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from corpai.rag import retrieve
from corpai.synth.personas import PERSONAS
from corpai.types import Level, Role, Trust
from tests.rag.helpers import owner_rows

pytestmark = pytest.mark.anyio

OPS = ("platform_admin", "auditor")
HUMANS = [k for k in PERSONAS if k not in OPS]


def docs(result) -> set[str]:
    return {h.doc_id for h in result.hits}


def cols(result) -> set[str]:
    return {h.collection for h in result.hits}


# ---- another department's collection never shows up --------------------------------------------------

async def test_finance_close_question_reaches_finance_only(personas):
    q = "month-end close procedure: reconcile bank and accruals, CFO sign-off"
    fin = await retrieve(personas["finance_staff"], q, purpose="qa")
    assert "fin-month-end-close" in docs(fin)
    for who in ("sales_staff", "eng_staff", "hr_staff", "legal_staff", "exec_staff", "platform_admin"):
        r = await retrieve(personas[who], q, purpose="qa")
        assert "finance" not in cols(r), who
        assert not docs(r) & {"fin-month-end-close", "fin-funding-memo-2026", "pb-finance-close"}, who


async def test_hr_interview_guide_is_hr_only_but_the_handbook_is_general(personas):
    guide_q = "structured interview guide questions for candidates"
    assert "hr-interview-guide" in docs(await retrieve(personas["hr_staff"], guide_q, purpose="qa"))
    eng = await retrieve(personas["eng_staff"], guide_q, purpose="qa")
    assert "hr" not in cols(eng) and "hr-interview-guide" not in docs(eng)
    leave = await retrieve(personas["eng_staff"], "HR handbook leave and absence, parental leave", purpose="qa")
    assert "hr-handbook-leave" in docs(leave)


async def test_acl_inside_a_collection_other_reps_account_plans(personas):
    r = await retrieve(personas["sales_staff"], "account plan Nordhafen Logistik", purpose="qa", k=20)
    plans = {d for d in docs(r) if d.startswith("sales-plan-")}
    assert plans and plans <= {"sales-plan-A-0002", "sales-plan-A-0004"}  # the two accounts u0011 owns
    head = await retrieve(personas["sales_head"], "account plan Nordhafen Logistik", purpose="qa", k=20)
    assert "sales-plan-A-0001" in docs(head)


async def test_eng_project_docs_need_project_membership(personas):
    q = "fp-coldchain README ADR runbook"
    staff = await retrieve(personas["eng_staff"], q, purpose="qa", k=30)
    member_of = {g.split(":", 1)[1] for g in personas["eng_staff"].groups if g.startswith("proj:")}
    rows = owner_rows("SELECT DISTINCT doc_id, project_id FROM rag.documents WHERE collection = 'eng' "
                      "AND project_id IS NOT NULL")
    foreign = {r["doc_id"] for r in rows if r["project_id"] not in member_of}
    assert foreign and not docs(staff) & foreign


# ---- shared collections -------------------------------------------------------------------------------

@pytest.mark.parametrize("who", HUMANS)
async def test_qa_reaches_general_and_legal_regs_for_every_employee(personas, who):
    gdpr = await retrieve(personas[who], "GDPR right to erasure of personal data", purpose="qa")
    assert "GDPR-Art-17" in docs(gdpr)
    policy = await retrieve(personas[who], "travel policy", purpose="qa")
    assert "gen-travel-policy" in docs(policy)


@pytest.mark.parametrize("who", OPS)
async def test_ops_roles_get_public_and_general_only(personas, who):
    """legal_regs is L1 / acl all, so RLS would let ops roles read it; only the policy filter keeps them
    out. This proves the WHERE clause works on its own, not just RLS."""
    r = await retrieve(personas[who], "GDPR right to erasure of personal data", purpose="qa", k=30)
    assert r.filter.collections == {"public", "general"}
    assert cols(r) <= {"public", "general"} and r.hits
    assert all(h.level <= Level.L2 for h in r.hits)


# ---- trust, status ----------------------------------------------------------------------------------------

async def test_report_purpose_returns_reviewed_documents_only(personas):
    """Trust is not part of RLS: the reviewed-only rule exists only in the filter."""
    for who in ("ceo", "sales_head", "legal_head", "finance_head"):
        r = await retrieve(personas[who], "account plan contract statement funding memo", purpose="report", k=30)
        assert r.hits, who
        assert {h.trust for h in r.hits} == {Trust.REVIEWED}, who
        assert not any(d.startswith(("sales-plan-", "C-0")) for d in docs(r)), who


async def test_superseded_versions_are_never_returned(personas):
    for who in PERSONAS:
        r = await retrieve(personas[who], "IT security policy passwords of at least 10 characters version 1",
                           purpose="qa", k=30)
        sec = [h for h in r.hits if h.doc_id == "gen-it-security-policy"]
        assert sec and all(h.version == 2 for h in sec), who


async def test_drafts_are_never_returned(personas):
    r = await retrieve(personas["finance_head"], "Quarterly statement 2026-Q3", purpose="qa", k=30)
    assert "fin-statement-2026-Q3" not in docs(r) and "fin-statement-2026-Q2" in docs(r)
    ceo = await retrieve(personas["ceo"], "Board update Q3 2026 draft", purpose="qa", k=30)
    assert "exec-board-update-2026-Q3" not in docs(ceo) and "exec-board-update-2026-Q2" in docs(ceo)


# ---- level aggregation -------------------------------------------------------------------------------------

async def test_canary_document_hit_makes_the_result_l4(personas):
    r = await retrieve(personas["legal_head"], "Donauzeile Frischdienst MSA", purpose="legal_review")
    c6 = [h for h in r.hits if h.doc_id == "C-0006"]
    assert c6 and all(h.level == Level.L4 for h in c6)
    assert r.level == Level.L4


async def test_result_level_is_the_max_hit_level(personas):
    for who, q in (("sales_staff", "battle card Tourenmeister"), ("eng_staff", "on-call handbook"),
                   ("hr_staff", "holiday days")):
        r = await retrieve(personas[who], q, purpose="qa")
        assert r.hits and r.level == max(h.level for h in r.hits)
        assert all(h.level <= personas[who].clearance for h in r.hits)


async def test_no_hits_is_l1(personas):
    r = await retrieve(personas["platform_admin"], "close checklist", purpose="qa", collections=["finance"])
    assert r.hits == [] and r.level == Level.L1


# ---- fail closed -----------------------------------------------------------------------------------------

async def test_inactive_principal_gets_nothing(personas):
    gone = replace(personas["finance_head"], active=False)
    r = await retrieve(gone, "month-end close procedure", purpose="qa")
    assert r.hits == [] and r.level == Level.L1 and r.filter.collections == frozenset()


async def test_service_account_gets_nothing(personas):
    svc = replace(personas["sales_staff"], user_id="svc:eval", role=Role.SERVICE, groups=frozenset({"svc:eval"}))
    r = await retrieve(svc, "travel policy", purpose="qa")
    assert r.hits == [] and r.filter.collections == frozenset()


async def test_requested_collections_can_narrow_but_never_widen(personas):
    r = await retrieve(personas["sales_staff"], "close checklist accruals", purpose="qa",
                       collections=["finance", "sales", "hr"])
    assert r.collections == {"sales"} and cols(r) <= {"sales"}


async def test_bad_arguments_are_refused(personas):
    p = personas["sales_staff"]
    with pytest.raises(ValueError):
        await retrieve(p, "x", purpose="anything_goes")
    with pytest.raises(ValueError):
        await retrieve(p, "x", purpose="qa", k=0)
    with pytest.raises(ValueError):
        await retrieve(p, "x", purpose="qa", k=1000)
    with pytest.raises(TypeError):
        await retrieve(p, "x", purpose="qa", collections="finance")
    with pytest.raises(TypeError):
        await retrieve(p, None, purpose="qa")  # type: ignore[arg-type]


async def test_empty_query_returns_nothing(personas):
    r = await retrieve(personas["sales_staff"], "   ", purpose="qa")
    assert r.hits == [] and r.level == Level.L1


# ---- audit ----------------------------------------------------------------------------------------------

async def test_retrieval_is_audited_without_query_or_text(personas):
    marker = "zebracrossing-7f3a"
    r = await retrieve(personas["legal_staff"], f"GDPR processor obligations {marker}", purpose="qa",
                       run_id="run_test_audit_1", agent="qa")
    row = owner_rows("SELECT * FROM audit.log WHERE event = 'retrieval' AND run_id = 'run_test_audit_1'")[0]
    assert row["actor"] == personas["legal_staff"].user_id and row["act_chain"] == ["agent:qa"]
    assert row["level"] == int(r.level) and row["decision"] == "allow"
    d = row["details"]
    assert d["purpose"] == "qa" and d["count"] == len(r.hits) and d["collections"] == sorted(r.collections)
    flat = repr(row)
    assert marker not in flat
    assert not any(h.text[:40] in flat for h in r.hits)


async def test_denied_retrieval_is_audited_as_deny(personas):
    gone = replace(personas["hr_staff"], active=False)
    await retrieve(gone, "interview guide", purpose="qa", run_id="run_test_audit_2")
    row = owner_rows("SELECT decision, reason FROM audit.log WHERE event = 'retrieval' AND run_id = 'run_test_audit_2'")
    assert row == [{"decision": "deny", "reason": "inactive"}]


# ---- hybrid ranking ------------------------------------------------------------------------------------------

async def test_full_text_finds_exact_identifiers(personas):
    r = await retrieve(personas["legal_head"], "C-0005", purpose="legal_review")
    assert r.hits[0].doc_id == "C-0005"


async def test_hits_carry_citation_fields(personas):
    r = await retrieve(personas["eng_staff"], "travel policy", purpose="qa")
    h = next(h for h in r.hits if h.doc_id == "gen-travel-policy")
    assert h.title == "Travel policy" and h.version == 1 and h.collection == "general"
    assert h.source_uri.startswith("https://") and h.chunk_id.startswith("gen-travel-policy@v1#")
    assert h.score > 0 and isinstance(h.level, Level) and isinstance(h.trust, Trust)
    assert [x.score for x in r.hits] == sorted((x.score for x in r.hits), reverse=True)


async def test_chinese_query_with_the_fake_embedder(personas):
    """Limitation of the fake embedder: Chinese finds English only through shared tokens. "GDPR" and "17"
    are shared, so Article 17 ranks first; a purely Chinese question about the travel policy shares
    nothing, so the policy is not ranked first. Real cross-lingual retrieval: CORPAI_EMBED=fastembed."""
    hit = await retrieve(personas["eng_staff"], "GDPR 第17条 删除权是什么", purpose="qa")
    assert hit.hits[0].doc_id == "GDPR-Art-17"
    miss = await retrieve(personas["eng_staff"], "差旅政策是什么", purpose="qa")
    assert miss.hits[0].doc_id != "gen-travel-policy"


async def test_strict_filter_still_fills_k_through_the_hnsw_index(personas, monkeypatch):
    """pgvector 0.6 filters after the HNSW candidate list. Force the index and check that a narrow filter
    still returns every readable chunk: ef_search must be high enough (issues-log). With ef_search = 5
    this returns 0 rows."""
    from contextlib import asynccontextmanager

    import sys

    from corpai.db import pg as real_pg

    retrieve_mod = sys.modules["corpai.rag.retrieve"]  # the package re-exports a function of the same name

    class _ForceIndex:
        def __getattr__(self, name):
            return getattr(real_pg, name)

        @staticmethod
        @asynccontextmanager
        async def principal_tx(p, *, run_id=None):
            async with real_pg.principal_tx(p, run_id=run_id) as conn:
                # without these the planner prefers the GIN index on acl_groups at this corpus size
                for knob in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
                    await conn.execute(f"SET LOCAL {knob} = off")
                yield conn

    monkeypatch.setattr(retrieve_mod, "pg", _ForceIndex())
    p = personas["sales_staff"]
    with real_pg.principal_tx_sync(p) as conn:
        readable = conn.execute("SELECT count(*) AS n FROM rag.chunks WHERE collection = 'sales' "
                                "AND status = 'current'").fetchone()["n"]
    # a query that matches no word, so only the vector arm (the index) contributes
    r = await retrieve(p, "qqqzzz xxyyzz", purpose="qa", collections=["sales"], k=50)
    assert readable > 0 and len(r.hits) == readable
