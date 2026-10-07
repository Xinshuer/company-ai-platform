"""Ingestion: labels (rule 4, never below the front matter), versioning, idempotency, fail-closed refusals."""

from __future__ import annotations

import shutil
from dataclasses import replace

import psycopg
import pytest

from corpai import audit
from corpai.classify import GazetteerError, classify_text, set_gazetteer
from corpai.classify import names as names_mod
from corpai.config import settings
from corpai.db import pg as pgmod
from corpai.rag import DocIn, IngestError, doc_level, ingest_corpus, ingest_document, load_corpus
from corpai.types import Level, Trust
from tests.rag.helpers import fresh_id, label, owner_rows


def _ingest(doc: DocIn) -> str:
    with pgmod.connect_owner() as conn:
        return ingest_document(conn, doc)


def _doc(doc_id: str, text: str, **kw) -> DocIn:
    base = dict(doc_id=doc_id, title=kw.pop("title", "Test document"), text=text, collection="general",
                source="general.policy", label=label(2))
    base.update(kw)
    return DocIn(**base)


def _versions(doc_id: str) -> list[dict]:
    return owner_rows("SELECT version, status, level, acl_groups FROM rag.documents WHERE doc_id = %s "
                      "ORDER BY version", (doc_id,))


# ---- the seeded corpus ---------------------------------------------------------------------------

def test_every_corpus_file_is_ingested_with_its_status(seeded):
    files = load_corpus(settings().corpus_dir)
    stored = {(r["doc_id"], r["status"]) for r in owner_rows("SELECT doc_id, status FROM rag.documents")}
    assert {(d.doc_id, d.status) for d in files} <= stored
    assert owner_rows("SELECT count(*) AS n FROM rag.documents d WHERE NOT EXISTS "
                      "(SELECT 1 FROM rag.chunks c WHERE c.doc_id = d.doc_id AND c.version = d.version)")[0]["n"] == 0


def test_no_document_is_labelled_below_its_front_matter_and_canaries_are_l4(seeded):
    files = {(d.doc_id, d.version): d for d in load_corpus(settings().corpus_dir)}
    rows = owner_rows("SELECT doc_id, version, level, acl_groups, trust FROM rag.documents")
    by_key = {(r["doc_id"], r["version"]): r for r in rows}
    for key, d in files.items():
        r = by_key[key]
        assert r["level"] >= int(d.label.level), key
        assert sorted(r["acl_groups"]) == sorted(d.label.acl_groups), key
        assert r["trust"] == str(d.label.trust), key
        if "KB-CANARY-" in d.text:
            assert r["level"] == 4, key
    # and none is over-labelled: front matter, source defaults and classification agree on the corpus
    # (issues-log I-01); a rise here would hide documents from people who should see them.
    raised = sorted(k for k, d in files.items() if by_key[k]["level"] != int(d.label.level))
    assert raised == []


def test_chunks_copy_the_document_label(seeded):
    bad = owner_rows(
        "SELECT c.chunk_id FROM rag.chunks c JOIN rag.documents d USING (doc_id, version) WHERE "
        "c.level <> d.level OR c.acl_groups <> d.acl_groups OR c.trust <> d.trust OR c.status <> d.status "
        "OR c.collection <> d.collection OR c.dept IS DISTINCT FROM d.dept")
    assert bad == []


def test_superseded_version_is_kept_and_marked(seeded):
    rows = _versions("gen-it-security-policy")
    assert [(r["version"], r["status"]) for r in rows] == [(1, "superseded"), (2, "current")]
    st = owner_rows("SELECT DISTINCT version, status FROM rag.chunks WHERE doc_id = 'gen-it-security-policy' "
                    "ORDER BY version")
    assert [(r["version"], r["status"]) for r in st] == [(1, "superseded"), (2, "current")]


def test_rerunning_the_corpus_is_idempotent_and_audited(seeded):
    before = owner_rows("SELECT count(*) AS d, (SELECT count(*) FROM rag.chunks) AS c FROM rag.documents")[0]
    assert ingest_corpus(settings().corpus_dir) == 0
    after = owner_rows("SELECT count(*) AS d, (SELECT count(*) FROM rag.chunks) AS c FROM rag.documents")[0]
    assert after == before
    job = owner_rows("SELECT details FROM audit.log WHERE event = 'owner_job' AND actor = 'svc:ingest' "
                     "ORDER BY seq DESC LIMIT 1")[0]["details"]
    assert job["inserted"] == 0 and job["unchanged"] == job["files"] > 0 and job["chunks_written"] == 0
    assert job["gazetteer_names"] > 0
    assert audit.verify_chain().ok


# ---- labels (rule 4) ------------------------------------------------------------------------------

def test_level_never_drops_below_front_matter_or_source(gazetteer, cleanup_docs):
    a, b, c = fresh_id(), fresh_id(), fresh_id()
    cleanup_docs += [a, b, c]
    _ingest(_doc(a, "Bring your badge.", label=label(1)))                  # source general.policy = L2
    _ingest(_doc(b, "Bring your badge.", label=label(3)))                  # producer said L3: kept
    _ingest(_doc(c, "Bring your badge.", source="no.such.source"))        # unknown source → L4
    assert [_versions(x)[0]["level"] for x in (a, b, c)] == [2, 3, 4]


def test_content_raises_the_level(gazetteer, cleanup_docs, personas):
    canary, name, secret = fresh_id(), fresh_id(), fresh_id()
    cleanup_docs += [canary, name, secret]
    _ingest(_doc(canary, "Reference KB-CANARY-0A1B2C3D in the appendix."))
    _ingest(_doc(name, f"Ask {personas['sales_staff'].name} about the renewal."))
    _ingest(_doc(secret, "Service login\npassword: Sup3r-Secret-Value-991\n"))
    assert [_versions(x)[0]["level"] for x in (canary, name, secret)] == [4, 3, 4]
    stored = owner_rows("SELECT text FROM rag.chunks WHERE doc_id = %s", (secret,))[0]["text"]
    assert "Sup3r-Secret-Value-991" not in stored and "REDACTED" in stored
    doc_text = owner_rows("SELECT text FROM rag.documents WHERE doc_id = %s", (secret,))[0]["text"]
    assert "Sup3r-Secret-Value-991" not in doc_text


def test_public_flag_only_for_reviewed_public_collections(gazetteer):
    text = "ABC Tech raised EUR 18,000,000 in a Series B round led by fund investors."
    pub = _doc("x", text, collection="public", source="public.press", label=label(1, trust=Trust.REVIEWED))
    assert doc_level(pub) == Level.L1
    # the same text without the reviewed trust is not treated as published content
    unrev = replace(pub, label=label(1, trust=Trust.INTERNAL))
    expected = classify_text(pub.title + "\n\n" + text).level
    assert expected > Level.L1 and doc_level(unrev) == expected


# ---- versioning ------------------------------------------------------------------------------------

def test_same_content_is_skipped_new_content_is_a_new_version(gazetteer, cleanup_docs):
    d = fresh_id()
    cleanup_docs.append(d)
    assert _ingest(_doc(d, "## Rule\nVersion one text.")) == f"{d}@v1"
    n_chunks = owner_rows("SELECT count(*) AS n FROM rag.chunks WHERE doc_id = %s", (d,))[0]["n"]
    assert _ingest(_doc(d, "## Rule\nVersion one text.")) == f"{d}@v1"
    assert owner_rows("SELECT count(*) AS n FROM rag.chunks WHERE doc_id = %s", (d,))[0]["n"] == n_chunks
    assert _ingest(_doc(d, "## Rule\nVersion two text.")) == f"{d}@v2"
    assert [(r["version"], r["status"]) for r in _versions(d)] == [(1, "superseded"), (2, "current")]
    chunk_status = owner_rows("SELECT version, status FROM rag.chunks WHERE doc_id = %s ORDER BY version", (d,))
    assert {(r["version"], r["status"]) for r in chunk_status} == {(1, "superseded"), (2, "current")}
    # sending the current text again creates nothing
    assert _ingest(_doc(d, "## Rule\nVersion two text.")) == f"{d}@v2"
    assert len(_versions(d)) == 2


def test_label_change_with_same_text_updates_in_place_and_cascades(gazetteer, cleanup_docs):
    d = fresh_id()
    cleanup_docs.append(d)
    _ingest(_doc(d, "Team notes.", label=label(2, acl=("all",))))
    # the ACL was narrowed (e.g. the doc moved to a department): every chunk must follow at once
    assert _ingest(_doc(d, "Team notes.", label=label(2, acl=("dept:finance", "head:finance")))) == f"{d}@v1"
    acls = owner_rows("SELECT DISTINCT acl_groups FROM rag.chunks WHERE doc_id = %s", (d,))
    assert [sorted(r["acl_groups"]) for r in acls] == [["dept:finance", "head:finance"]]
    assert len(_versions(d)) == 1


def test_draft_does_not_replace_the_current_version(gazetteer, cleanup_docs):
    d = fresh_id()
    cleanup_docs.append(d)
    _ingest(_doc(d, "Published text."))
    _ingest(_doc(d, "Draft text.", status="draft"))
    assert [(r["version"], r["status"]) for r in _versions(d)] == [(1, "current"), (2, "draft")]


def test_bad_documents_are_refused(gazetteer, cleanup_docs):
    d = fresh_id()
    cleanup_docs.append(d)
    for bad in (_doc(d, "x", collection="secret_stuff"), _doc(d, "x", status="published"),
                _doc(d, "x", label=label(2, acl=()))):
        with pytest.raises(IngestError):
            _ingest(bad)
    assert _versions(d) == []


def test_app_role_cannot_write_rag(seeded, personas):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(personas["ceo"]) as conn:
            conn.execute("UPDATE rag.chunks SET level = 1")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(personas["platform_admin"]) as conn:
            conn.execute("DELETE FROM rag.documents")


# ---- fail closed on the gazetteer --------------------------------------------------------------------

@pytest.fixture
def small_corpus(seeded, tmp_path):
    """A copy of the corpus plus one new document, so a refused run would visibly have written something."""
    dst = tmp_path / "corpus"
    shutil.copytree(settings().corpus_dir / "docs", dst / "docs")
    doc_id = fresh_id("gaz")
    (dst / "docs" / "general" / f"{doc_id}.md").write_text(
        f"---\ndoc_id: {doc_id}\ntitle: Gazetteer probe\ncollection: general\nsource: general.policy\nlevel: 2\n"
        "acl_groups: [all]\ntrust: reviewed\nversion: 1\nstatus: current\n---\n\n# Probe\nText.\n",
        encoding="utf-8", newline="\n")
    yield dst, doc_id
    with pgmod.connect_owner() as conn:
        conn.execute("DELETE FROM rag.documents WHERE doc_id = %s", (doc_id,))


def test_ingest_corpus_refuses_when_the_gazetteer_cannot_load(small_corpus, monkeypatch):
    corpus, doc_id = small_corpus

    def broken() -> int:
        raise GazetteerError("hr.candidates is not readable")

    monkeypatch.setattr("corpai.rag.ingest.load_gazetteer_from_db", broken)
    with pytest.raises(IngestError, match="gazetteer"):
        ingest_corpus(corpus)
    assert _versions(doc_id) == []


def test_ingest_document_refuses_without_a_gazetteer(seeded, monkeypatch, cleanup_docs):
    monkeypatch.setattr(names_mod, "_state", names_mod._state)  # restored after the test
    set_gazetteer([])
    d = fresh_id()
    cleanup_docs.append(d)
    with pytest.raises(IngestError, match="gazetteer"):
        _ingest(_doc(d, "Ask the account owner."))
    assert _versions(d) == []


def test_ingest_corpus_loads_the_gazetteer_itself(small_corpus, monkeypatch):
    corpus, doc_id = small_corpus
    monkeypatch.setattr(names_mod, "_state", names_mod._state)
    set_gazetteer([])
    assert ingest_corpus(corpus) == 1  # only the probe document is new
    assert names_mod.gazetteer_size() > 0
    assert [r["version"] for r in _versions(doc_id)] == [1]


def test_malformed_front_matter_aborts_the_run(small_corpus):
    corpus, doc_id = small_corpus
    (corpus / "docs" / "general" / "broken.md").write_text("---\ndoc_id: broken\n", encoding="utf-8")
    with pytest.raises(IngestError, match="broken.md"):
        ingest_corpus(corpus)
    assert _versions(doc_id) == []


def test_missing_corpus_is_a_clear_error(seeded, tmp_path):
    with pytest.raises(IngestError, match="synth"):
        ingest_corpus(tmp_path / "nothing")


def test_documents_missing_from_the_corpus_are_retired(seeded, tmp_path):
    """Review fix: a doc_id that left the corpus (synth --small after a full run, a deleted file) is
    superseded, so Q&A stops citing it; putting it back restores it."""
    src = settings().corpus_dir / "docs"
    dst = tmp_path / "corpus"
    shutil.copytree(src, dst / "docs")
    victim = sorted((dst / "docs" / "general").glob("*.md"))[0]
    doc_id = victim.stem.split(".v")[0]
    for f in (dst / "docs" / "general").glob(f"{doc_id}*.md"):
        f.unlink()
    assert owner_rows("SELECT count(*) AS n FROM rag.documents WHERE doc_id = %s AND status = 'current'",
                      (doc_id,))[0]["n"] == 1
    try:
        ingest_corpus(dst)
        assert {r["status"] for r in _versions(doc_id)} == {"superseded"}
        assert owner_rows("SELECT count(*) AS n FROM rag.chunks WHERE doc_id = %s AND status <> 'superseded'",
                          (doc_id,))[0]["n"] == 0
        job = owner_rows("SELECT details FROM audit.log WHERE event = 'owner_job' AND actor = 'svc:ingest' "
                         "ORDER BY seq DESC LIMIT 1")[0]["details"]
        assert job["retired"] == 1
    finally:
        ingest_corpus(settings().corpus_dir)
    assert "current" in {r["status"] for r in _versions(doc_id)}
