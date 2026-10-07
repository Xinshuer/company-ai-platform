"""Corpus front matter, per-collection labels, versioning, and the web mock corpus."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

from corpai.config import settings
from corpai.db import pg as pgmod

KEYS = ["doc_id", "title", "collection", "source", "level", "dept", "owner_id", "acl_groups", "trust", "subject_ids",
        "project_id", "matter_id", "version", "status", "source_uri"]
COLLECTIONS = {"public", "general", "eng", "sales", "legal", "legal_regs", "finance", "hr", "exec", "playbooks"}


def parse(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    assert b"\r\n" not in raw, f"{path} has CRLF line endings"
    text = raw.decode("utf-8")
    assert text.startswith("---\n"), path
    _, front, body = text.split("---\n", 2)
    return yaml.safe_load(front), body


@pytest.fixture(scope="module")
def docs(synth_small) -> list[tuple[Path, dict, str]]:
    root = settings().corpus_dir / "docs"
    return [(p, *parse(p)) for p in sorted(root.rglob("*.md"))]


def test_report_lists_every_file(synth_small, docs):
    root = settings().corpus_dir
    assert sorted(synth_small.doc_paths) == sorted(p.relative_to(root).as_posix() for p, _, _ in docs)


def test_front_matter_is_complete_and_typed(docs):
    assert len(docs) >= 80
    for path, m, body in docs:
        assert list(m) == KEYS, path
        assert m["collection"] == path.parent.name and m["collection"] in COLLECTIONS
        assert m["level"] in (1, 2, 3, 4) and m["trust"] in ("reviewed", "internal", "untrusted")
        assert m["status"] in ("current", "superseded", "draft") and isinstance(m["version"], int)
        assert isinstance(m["acl_groups"], list) and m["acl_groups"] and all(isinstance(g, str) for g in m["acl_groups"])
        assert isinstance(m["subject_ids"], list)
        assert all("," not in g for g in m["acl_groups"])
        assert m["source_uri"].startswith("https://") and m["title"] and body.strip()
        if m["level"] >= 3:
            assert "all" not in m["acl_groups"], path
        expected_name = m["doc_id"] if m["status"] != "superseded" else f"{m['doc_id']}.v{m['version']}"
        assert path.stem == expected_name


def test_collections_present(docs):
    assert {m["collection"] for _, m, _ in docs} == COLLECTIONS


def test_doc_versions_unique_and_one_superseded_policy(docs):
    keys = Counter((m["doc_id"], m["version"]) for _, m, _ in docs)
    assert max(keys.values()) == 1
    superseded = [m for _, m, _ in docs if m["status"] == "superseded"]
    assert superseded, "need an old policy version"
    for old in superseded:
        current = [m for _, m, _ in docs if m["doc_id"] == old["doc_id"] and m["status"] == "current"]
        assert len(current) == 1 and current[0]["version"] > old["version"]
        assert old["collection"] == "general"


@pytest.mark.parametrize("collection,level,acl,trust", [
    ("public", {1}, ["all"], "reviewed"), ("legal_regs", {1}, ["all"], "reviewed"),
    ("general", {2}, ["all"], "reviewed"), ("playbooks", {2, 3}, None, "reviewed"),
])
def test_collection_labels(docs, collection, level, acl, trust):
    rows = [m for _, m, _ in docs if m["collection"] == collection]
    assert rows
    for m in rows:
        assert m["level"] in level and m["trust"] == trust
        if acl is not None:
            assert m["acl_groups"] == acl
        else:
            assert set(m["acl_groups"]) == {f"dept:{m['dept']}", f"head:{m['dept']}", "role:ceo"}


def test_restricted_documents(docs):
    by_id = {m["doc_id"]: m for _, m, _ in docs}
    for mid in ("M-01", "M-02"):
        memo = by_id[f"legal-{mid}-memo"]
        assert memo["level"] == 4
        assert set(memo["acl_groups"]) == {f"matter:{mid}", "head:legal", f"grant:matter:{mid}"}
        assert memo["matter_id"] == mid
    for d in by_id.values():
        if d["source"] in ("finance.statement", "finance.funding"):
            assert d["level"] == 4 and set(d["acl_groups"]) == {"head:finance", "dept:finance", "role:ceo"}
        if d["collection"] == "exec":
            assert d["level"] == 4 and "role:ceo" in d["acl_groups"]
        if d["collection"] == "eng" and d["source"] == "eng.repo":
            # repo docs are L3; the one hosting a canary is L4 with the same ACL (§5.1)
            assert d["level"] == (4 if d["doc_id"] == "eng-P-01-readme" else 3), d["doc_id"]
            assert set(d["acl_groups"]) == {f"proj:{d['project_id']}", "head:eng"}
        if d["collection"] == "sales" and d["source"] == "sales.account_plan":
            assert d["level"] == 3 and "head:sales" in d["acl_groups"] and d["subject_ids"]


def test_contract_mirrors_match_rows(synth_small, docs):
    with pgmod.connect_owner() as c:
        rows = {r["contract_id"]: r for r in c.execute("SELECT * FROM legal.contracts").fetchall()}
    mirrored = {m["doc_id"]: (m, body) for _, m, body in docs if m["source"] == "legal.contract"}
    assert mirrored.keys() == rows.keys()
    for cid, (m, body) in mirrored.items():
        r = rows[cid]
        assert m["level"] == r["level"] and m["acl_groups"] == list(r["acl_groups"]) and m["trust"] == r["trust"]
        assert r["text"] in body


def test_resumes_are_not_in_the_corpus(synth_small, docs):
    with pgmod.connect_owner() as c:
        cands = c.execute("SELECT name, email FROM hr.candidates").fetchall()
    blob = "\n".join(body for _, _, body in docs)
    assert "Photo attached" not in blob
    for cand in cands:
        assert cand["email"] not in blob and cand["name"] not in blob


def test_web_pages(synth_small):
    path = settings().corpus_dir / "web" / "pages.jsonl"
    assert b"\r\n" not in path.read_bytes()
    pages = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len({p["url"] for p in pages}) == len(pages)
    for p in pages:
        assert list(p) == ["url", "domain", "title", "text", "published_on", "company_account_id", "kind", "injected"]
        assert p["kind"] in ("site", "news", "funding", "blog") and isinstance(p["injected"], bool)
        assert p["domain"].endswith(".example") and p["url"].startswith("https://")
        host = p["url"].split("/")[2]
        assert host.endswith(".example") and host.endswith(p["domain"])
        assert "2025-10-01" <= p["published_on"] <= "2026-09-30"
    with pgmod.connect_owner() as c:
        accounts = {r["account_id"] for r in c.execute("SELECT account_id FROM crm.accounts").fetchall()}
    kinds: dict[str, set[str]] = {}
    for p in pages:
        if p["company_account_id"]:
            kinds.setdefault(p["company_account_id"], set()).add(p["kind"])
    assert set(kinds) == accounts
    assert all(k >= {"site", "news", "funding"} for k in kinds.values())
    assert sum(p["injected"] for p in pages) >= 3
    assert any(p["company_account_id"] is None for p in pages)  # a few industry pages


def test_public_and_internal_docs_hold_no_personal_data(synth_small, docs):
    """L1/L2 documents must stay L1/L2 after content classification (rule 1: personal data is at least L3),
    so they carry no email addresses and no names of staff, contacts or candidates."""
    with pgmod.connect_owner() as c:
        names = {r["name"] for t in ("core.users", "crm.contacts", "hr.candidates")
                 for r in c.execute(f"SELECT name FROM {t}").fetchall()}
    names = {n for n in names if " " in n}  # people, not service accounts
    for path, m, body in docs:
        if m["level"] <= 2:
            assert "@" not in body, path
            assert not [n for n in names if n in body], path


def test_company_wide_docs_sit_in_collections_every_employee_searches(docs, principal_for):
    """A doc labelled for everyone (acl all) is useless in a collection most people's qa scope
    never searches: the HR handbook in `hr`, the GDPR library outside everyone's scope. Every
    such doc must be reachable through qa for every active employee (design-review 五:
    公开资料、法规库 其他部门 读; handbook = internal policies for all)."""
    from corpai.policy import rag_filter
    from corpai.synth.personas import PERSONAS

    humans = [principal_for(uid) for key, uid in PERSONAS.items() if key not in ("platform_admin", "auditor")]
    company_wide = [m for _, m, _ in docs if set(m["acl_groups"]) == {"all"}]
    assert any(m["doc_id"].startswith("hr-handbook-") for m in company_wide)
    assert any(m["collection"] == "legal_regs" for m in company_wide)
    for p in humans:
        scope = rag_filter(p, purpose="qa").collections
        missing = sorted({m["collection"] for m in company_wide} - scope)
        assert not missing, (p.user_id, p.dept, missing)
