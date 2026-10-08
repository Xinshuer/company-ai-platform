"""call_tool and retrieve put the data subjects of what they read on their span (issues-log D-04), so an
erasure request reaches the run's trace; a data-less refusal records none."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from corpai.agents import call_tool, runtime
from corpai.mcp_client import ToolEnvelope
from corpai.policy import RagFilter
from corpai.rag import Hit, RetrievalResult
from corpai.types import Level, Trust
from tests.agents.fakes import FakeMcp, NullTracer, context

pytestmark = pytest.mark.anyio


def _env(ok=True, subjects=("CT-0042",)):
    return ToolEnvelope(ok=ok, data={"x": 1} if ok else None, error=None if ok else "tool_denied",
                        label={"level": 3, "trust": "internal", "sources": ["crm.contact:CT-0042"],
                               "subject_ids": list(subjects)})


async def test_tool_span_records_the_subjects_of_returned_rows():
    tracer = NullTracer()
    ctx = context(mcp=FakeMcp([_env(), _env(ok=False, subjects=("CT-0099",))]), tracer=tracer)
    await call_tool(SimpleNamespace(context=ctx), {}, "crm", "get_account", {"account_id": "A-0001"})
    await call_tool(SimpleNamespace(context=ctx), {}, "crm", "get_account", {"account_id": "A-0002"})
    assert [s.subjects for s in tracer.spans] == [{"CT-0042"}, set()]


async def test_retrieval_span_records_the_subjects_of_kept_hits(monkeypatch):
    import corpai.rag

    def hit(doc_id, subjects):
        return Hit(chunk_id=f"{doc_id}@v1#0", doc_id=doc_id, version=1, title=doc_id, heading="", text=doc_id,
                   level=Level.L2, trust=Trust.INTERNAL, collection="sales", score=0.1, source_uri="",
                   subject_ids=subjects)

    async def fake_retrieve(p, query, *, purpose, k, collections, run_id, agent):
        return RetrievalResult(hits=[hit("plan-a", ("CT-0001", "CT-0002")), hit("plan-b", ("CT-0003",))],
                               level=Level.L2, filter=RagFilter(frozenset(), Level.L3, frozenset(), None),
                               collections=frozenset({"sales"}))

    monkeypatch.setattr(corpai.rag, "retrieve", fake_retrieve)
    tracer = NullTracer()
    ctx = context(tracer=tracer)
    await runtime.retrieve(SimpleNamespace(context=ctx), {}, "plan", purpose="qa", keep=lambda h: h.doc_id == "plan-a")
    assert tracer.spans[0].subjects == {"CT-0001", "CT-0002"}  # a dropped hit never entered the run
