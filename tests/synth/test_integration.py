"""Synth against the streams that consume it: the owner-job audit event (A1) and the content classifier
(A2). Both modules land in parallel, so each test skips cleanly when its module is not there yet."""

from __future__ import annotations

import pytest

from corpai.db import pg as pgmod


def test_owner_job_is_audited(synth_small):
    pytest.importorskip("corpai.audit")
    from corpai import synth

    report = synth.run(seed=7, small=True)
    with pgmod.connect_owner() as c:
        row = c.execute("SELECT actor, event, details, details::text AS raw FROM audit.log "
                        "WHERE event = 'owner_job' AND actor = 'svc:synth' ORDER BY seq DESC LIMIT 1").fetchone()
    assert row is not None, "synth.run must record an owner_job audit event"
    assert row["details"]["seed"] == 7 and row["details"]["small"] is True
    assert row["details"]["rows"] == sum(report.counts.values())
    # the audit log is readable by the security auditor: it must not leak eval answers or canaries
    assert "KB-CANARY-" not in row["raw"] and "expected" not in row["details"]


def test_corpus_labels_agree_with_the_classifier(synth_small):
    """Ingestion labels a chunk max(source label, classify_text) (rule 4). Public and internal documents must
    stay routable to `external` (<= L2), and L3 documents only reach L4 where a canary is planted."""
    classify = pytest.importorskip("corpai.classify")
    from corpai import synth

    world = synth.generate(7, small=False)
    canary_docs = {c["location"][len("file:"):] for c in world.canaries if c["location"].startswith("file:")}
    problems = []
    for d in world.docs:
        got = int(classify.classify_text(d.body).level)
        if d.level <= 2 and got > 2:
            problems.append((d.doc_id, d.level, got))
        elif d.level == 3 and got > 3 and d.relpath not in canary_docs:
            problems.append((d.doc_id, d.level, got))
    assert problems == []


def test_public_db_rows_agree_with_the_classifier(synth_small):
    classify = pytest.importorskip("corpai.classify")
    with pgmod.connect_owner() as c:
        rows = [(r["id"], r["level"], r["text"]) for r in c.execute(
            "SELECT reg_id AS id, level, summary AS text FROM legal.regulations "
            "UNION ALL SELECT req_id, level, jd_text FROM hr.requisitions").fetchall()]
    assert rows
    assert [(i, lvl) for i, lvl, text in rows if int(classify.classify_text(text).level) > lvl] == []
