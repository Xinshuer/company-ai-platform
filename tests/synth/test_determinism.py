"""Same seed → byte-identical corpus and identical tables; runs are idempotent; persona ids are stable."""

from __future__ import annotations

import time
from typing import Any

from psycopg.types.json import Jsonb

from corpai import synth
from corpai.synth.personas import PERSONA_SPECS, PERSONAS
from corpai.synth.writer import TABLES, render_doc


def test_two_runs_same_seed_identical(synth_small, checksums, corpus_digest):
    first_tables, first_files = checksums(TABLES), corpus_digest()
    t0 = time.perf_counter()
    report = synth.run(seed=7, small=True)
    elapsed = time.perf_counter() - t0
    assert checksums(TABLES) == first_tables  # also proves idempotence: no duplicate rows after a rerun
    assert corpus_digest() == first_files
    assert report.counts == synth_small.counts
    assert elapsed < 20, f"small run took {elapsed:.1f}s"


def test_core_directory_tables_are_part_of_the_checksum(synth_small, checksums):
    sums = checksums(["core.users", "core.memberships", "core.grants"])
    assert all(not v.startswith("0:") for v in sums.values())


def _plain(v: Any) -> Any:
    """Unwrap psycopg Jsonb (no __eq__, and its repr is truncated to ~35 chars) so values compare."""
    if isinstance(v, Jsonb):
        return _plain(v.obj)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def test_plain_sees_differences_inside_jsonb():
    """The helper must catch what repr() hid: two long Jsonb values differing past the cut-off."""
    a = Jsonb({"account_id": "A-0001", "account_name": "x" * 40, "amount": 1})
    b = Jsonb({"account_id": "A-0001", "account_name": "x" * 40, "amount": 2})
    assert repr(a) == repr(b)  # the trap the old test fell into
    assert _plain([{"e": a}]) != _plain([{"e": b}])


def test_full_generation_is_deterministic_in_memory():
    a, b = synth.generate(7, small=False), synth.generate(7, small=False)
    assert a.tables.keys() == b.tables.keys()
    for t in a.tables:
        assert _plain(a.tables[t]) == _plain(b.tables[t]), t
    assert [render_doc(d) for d in a.docs] == [render_doc(d) for d in b.docs]
    assert a.web_pages == b.web_pages
    assert _plain(a.planted) == _plain(b.planted)


def test_other_seed_changes_filler_but_not_personas():
    a, b = synth.generate(7, small=True), synth.generate(8, small=True)
    users_a = {u["user_id"]: u for u in a.tables["core.users"]}
    users_b = {u["user_id"]: u for u in b.tables["core.users"]}
    assert users_a.keys() == users_b.keys()
    for spec in PERSONA_SPECS:
        assert users_a[spec.user_id]["name"] == users_b[spec.user_id]["name"] == spec.name
        assert users_a[spec.user_id]["persona_key"] == spec.key
    assert any(users_a[u]["name"] != users_b[u]["name"] for u in users_a if users_a[u]["persona_key"] is None)
    canaries_a = {c["canary"] for c in a.canaries}
    canaries_b = {c["canary"] for c in b.canaries}
    assert canaries_a.isdisjoint(canaries_b)
    # planted refs are part of the script, so evals keep working for any seed
    assert [(p["kind"], p["ref"]) for p in a.planted] == [(p["kind"], p["ref"]) for p in b.planted]


def test_persona_ids_are_fixed():
    assert PERSONAS == {
        "ceo": "u0001", "exec_staff": "u0002", "sales_head": "u0010", "sales_staff": "u0011", "eng_head": "u0020",
        "eng_staff": "u0021", "legal_head": "u0030", "legal_staff": "u0031", "finance_head": "u0040",
        "finance_staff": "u0041", "hr_head": "u0050", "hr_staff": "u0051", "platform_admin": "u0060",
        "auditor": "u0061"}


def test_generator_source_never_reads_the_os_clock():
    """Business dates come from the script's fixed window, never from the machine clock or os.urandom."""
    import re
    from pathlib import Path

    root = Path(synth.__file__).parent
    banned = re.compile(r"date\.today\(|datetime\.now\(|utcnow\(|time\.time\(|os\.urandom|uuid4\(|clock\.now_utc|"
                        r"random\.(random|randint|choice|sample|shuffle|uniform)\(|date_time|date_between")
    offenders = [f"{p.name}:{n}" for p in root.glob("*.py")
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if banned.search(line)]
    assert offenders == []
