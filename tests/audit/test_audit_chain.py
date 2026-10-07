"""Hash chain: Python and SQL agree byte for byte, and tampering is pinpointed by both."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from corpai import audit
from corpai.audit import chain
from corpai.db import pg as pgmod
from corpai.ids import new_id


@contextmanager
def _superuser_with_triggers_off() -> Iterator[psycopg.Connection]:
    """What an attacker with superuser (or the table owner) could do: switch the guards off."""
    with psycopg.connect(pgmod.superuser_dsn(), row_factory=dict_row) as c:
        c.execute("ALTER TABLE audit.log DISABLE TRIGGER USER")
        try:
            yield c
        finally:
            c.execute("ALTER TABLE audit.log ENABLE TRIGGER USER")


def _both() -> tuple[audit.ChainReport, audit.ChainReport]:
    return audit.verify_chain(), audit.verify_chain_sql()


def _seed(n: int = 5) -> list[int]:
    run_id = new_id("run")
    return [audit.record("tool_call", actor="u0031", run_id=run_id, reason=f"r{i}",
                         details={"i": i}) for i in range(n)]


def test_python_canonical_matches_sql_for_tricky_rows(pg):
    seqs = [
        audit.record("model_call", actor="u0001", act_chain=["agent:qa", "sub:web", "ü\"\\\n\t"],
                     action="read", resource="doc:Ä/1", decision="allow", reason="Grüße, 你好   \x01",
                     route="external", level=2, run_id=new_id("run"),
                     details={"z": 1, "a": [1.5, 1e20, -0.0, None, True], "nested": {"k": "\"q\""},
                              "long_key_name": "x" * 300, "emoji": "\U0001F600"}),
        audit.record("login", actor="u0002"),  # mostly NULL fields, empty act_chain, empty details
        audit.record("break_glass", actor="u0003", act_chain=["a"], level=4,
                     details={"amount": 12345678901234567890, "f": 0.1}),
    ]
    with pgmod.connect_owner() as c:
        for seq in seqs:
            row = c.execute(f"SELECT {chain._COLUMNS}, audit.canonical(l) AS sql_canon "
                            "FROM audit.log l WHERE seq = %s", (seq,)).fetchone()
            assert chain.canonical(row) == bytes(row["sql_canon"])
            assert chain.row_hash(bytes(row["prev_hash"]), row) == bytes(row["hash"])


def test_chain_is_valid_and_both_verifiers_agree(pg):
    _seed(3)
    py, sql = _both()
    assert py.ok and sql.ok
    assert py == sql and py.checked > 0 and py.first_bad_seq is None


def test_verify_chain_accepts_a_given_connection(pg):
    _seed(1)
    with pgmod.connect_owner(autocommit=True) as c:
        assert audit.verify_chain(c).ok


def test_tampered_reason_is_found_by_python_and_sql(pg):
    seqs = _seed(5)
    target = seqs[2]
    with _superuser_with_triggers_off() as c:
        original = c.execute("SELECT reason FROM audit.log WHERE seq = %s", (target,)).fetchone()["reason"]
        c.execute("UPDATE audit.log SET reason = 'nothing to see here' WHERE seq = %s", (target,))
    try:
        py, sql = _both()
        assert not py.ok and py.first_bad_seq == target
        assert py == sql
    finally:
        with _superuser_with_triggers_off() as c:
            c.execute("UPDATE audit.log SET reason = %s WHERE seq = %s", (original, target))
    assert all(r.ok for r in _both())


@pytest.mark.parametrize("column,value", [
    ("details", '{"i": 999}'),
    ("act_chain", "{agent:evil}"),
    ("level", "1"),
    ("actor", "u9999"),
    ("ts", "2020-01-01T00:00:00Z"),
])
def test_any_field_change_is_detected(pg, column, value):
    seqs = _seed(3)
    target = seqs[1]
    with _superuser_with_triggers_off() as c:
        original = c.execute(f"SELECT {column}::text AS v FROM audit.log WHERE seq = %s", (target,)).fetchone()["v"]
        c.execute(f"UPDATE audit.log SET {column} = %s WHERE seq = %s", (value, target))
    try:
        py, sql = _both()
        assert py.first_bad_seq == target and py == sql
    finally:
        with _superuser_with_triggers_off() as c:
            c.execute(f"UPDATE audit.log SET {column} = %s WHERE seq = %s", (original, target))
    assert all(r.ok for r in _both())


def test_deleted_row_breaks_the_link_of_its_successor(pg):
    seqs = _seed(4)
    target, successor = seqs[1], seqs[2]
    with _superuser_with_triggers_off() as c:
        saved = c.execute("SELECT * FROM audit.log WHERE seq = %s", (target,)).fetchone()
        c.execute("DELETE FROM audit.log WHERE seq = %s", (target,))
    try:
        py, sql = _both()
        assert py.first_bad_seq == successor and py == sql
    finally:
        cols = list(saved)
        with _superuser_with_triggers_off() as c:
            c.execute(f"INSERT INTO audit.log ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                      [psycopg.types.json.Jsonb(saved[k]) if k == "details" else saved[k] for k in cols])
    assert all(r.ok for r in _both())


def test_rehashed_row_is_still_caught_by_the_next_link(pg):
    """An attacker who edits a row and recomputes its hash breaks the successor's prev_hash."""
    seqs = _seed(3)
    target, successor = seqs[0], seqs[1]
    with _superuser_with_triggers_off() as c:
        old = c.execute("SELECT reason, hash FROM audit.log WHERE seq = %s", (target,)).fetchone()
        c.execute("UPDATE audit.log SET reason = 'forged' WHERE seq = %s", (target,))
        c.execute("UPDATE audit.log l SET hash = sha256(l.prev_hash || audit.canonical(l)) WHERE seq = %s",
                  (target,))
    try:
        py, sql = _both()
        assert py.first_bad_seq == successor and py == sql
    finally:
        with _superuser_with_triggers_off() as c:
            c.execute("UPDATE audit.log SET reason = %s, hash = %s WHERE seq = %s",
                      (old["reason"], old["hash"], target))
    assert all(r.ok for r in _both())


# ---- anchors: what the links alone cannot catch ----------------------------------------------------

def _rows_from(seq: int) -> list[dict]:
    with pgmod.connect_owner() as c:
        return c.execute("SELECT * FROM audit.log WHERE seq >= %s ORDER BY seq", (seq,)).fetchall()


def _reinsert(rows: list[dict]) -> None:
    with _superuser_with_triggers_off() as c:
        for saved in rows:
            cols = list(saved)
            c.execute(f"INSERT INTO audit.log ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                      [psycopg.types.json.Jsonb(saved[k]) if k == "details" else saved[k] for k in cols])


def test_cut_tail_passes_the_links_but_not_the_anchors(pg, tmp_path):
    path = tmp_path / "anchors.jsonl"
    first = _seed(2)
    a1 = audit.write_anchor(path=path)
    rest = _seed(3)
    a2 = audit.write_anchor(path=path)
    assert (a1.seq, a2.seq) == (first[-1], rest[-1])
    anchors = audit.read_anchors(path)
    saved = _rows_from(rest[0])
    with _superuser_with_triggers_off() as c:
        c.execute("DELETE FROM audit.log WHERE seq >= %s", (rest[0],))
    try:
        # the gap review found: without anchors the shortened chain is perfectly valid
        assert audit.verify_chain(anchors=[]).ok and audit.verify_chain_sql(anchors=[]).ok
        py, sql = audit.verify_chain(anchors=anchors), audit.verify_chain_sql(anchors=anchors)
        assert not py.ok and py == sql
        assert py.first_bad_seq == rest[0]  # one past the last anchor that still matches
    finally:
        _reinsert(saved)
    assert audit.verify_chain(anchors=anchors).ok and audit.verify_chain_sql(anchors=anchors).ok


def test_rewritten_and_rehashed_history_passes_the_links_but_not_the_anchors(pg, tmp_path):
    path = tmp_path / "anchors.jsonl"
    _seed(2)
    a1 = audit.write_anchor(path=path)
    later = _seed(3)
    audit.write_anchor(path=path)
    anchors = audit.read_anchors(path)
    target = later[0]
    saved = _rows_from(target)
    with _superuser_with_triggers_off() as c:
        c.execute("UPDATE audit.log SET reason = 'forged' WHERE seq = %s", (target,))
        for row in saved:  # recompute every link from the forged row to the end, as an owner can
            c.execute("UPDATE audit.log l SET prev_hash = (SELECT p.hash FROM audit.log p WHERE p.seq < l.seq "
                      "ORDER BY p.seq DESC LIMIT 1) WHERE l.seq = %s", (row["seq"],))
            c.execute("UPDATE audit.log l SET hash = sha256(l.prev_hash || audit.canonical(l)) WHERE l.seq = %s",
                      (row["seq"],))
    try:
        assert audit.verify_chain(anchors=[]).ok and audit.verify_chain_sql(anchors=[]).ok
        py, sql = audit.verify_chain(anchors=anchors), audit.verify_chain_sql(anchors=anchors)
        assert not py.ok and py == sql and py.first_bad_seq == a1.seq + 1 == target
    finally:
        with _superuser_with_triggers_off() as c:
            for row in saved:
                c.execute("UPDATE audit.log SET reason = %s, prev_hash = %s, hash = %s WHERE seq = %s",
                          (row["reason"], row["prev_hash"], row["hash"], row["seq"]))
    assert audit.verify_chain(anchors=anchors).ok


def test_edited_anchor_file_is_refused(pg, tmp_path, monkeypatch):
    from corpai.audit import anchor as anchor_mod

    path = tmp_path / "anchors.jsonl"
    _seed(1)
    audit.write_anchor(path=path)
    line = path.read_text(encoding="utf-8")
    import json as _json

    obj = _json.loads(line)
    obj["seq"] = obj["seq"] - 1  # point the anchor at an older row: the MAC no longer matches
    path.write_text(_json.dumps(obj) + "\n", encoding="utf-8")
    with pytest.raises(audit.AnchorError):
        audit.read_anchors(path)
    monkeypatch.setattr(anchor_mod, "anchor_path", lambda: path)
    with pytest.raises(audit.AnchorError):
        audit.verify_chain()  # the default anchor file is checked too, never silently skipped
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(audit.AnchorError):
        audit.read_anchors(path)


def test_owner_jobs_anchor_the_head(pg):
    seq = audit.record_owner_job("anchor-test")
    assert audit.read_anchors()[-1].seq >= seq
    with pgmod.connect_owner() as c:
        stored = bytes(c.execute("SELECT hash FROM audit.log WHERE seq = %s", (seq,)).fetchone()["hash"])
    assert any(a.seq == seq and a.hash == stored for a in audit.read_anchors())
    py, sql = _both()
    assert py.ok and py == sql
