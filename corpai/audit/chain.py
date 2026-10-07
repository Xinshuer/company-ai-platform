"""Hash-chain verification for audit.log, independent of the SQL implementation.

The canonical serialisation is defined in migrations/001_a1_audit_log.sql (audit.canonical) and
reproduced here byte for byte; tests check that both agree on every row, including after tampering.

The links alone cannot catch whoever controls the table: a cut tail, or a rewritten row re-hashed
together with every row after it, still forms a valid chain. Both verifiers therefore also check
the anchors of corpai/audit/anchor.py (heads of the chain stored outside the database): every
anchored seq must still be there with exactly the anchored hash.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row

from corpai.audit.anchor import Anchor, read_anchors

ZERO_HASH = bytes(32)
_VERSION = b"corpai.audit.v1"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_US = timedelta(microseconds=1)
_BATCH = 2000

_COLUMNS = ("seq, ts, actor, act_chain, event, action, resource, decision, reason, route, level, "
            "approval_id, run_id, details::text AS details_text, prev_hash, hash")


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    checked: int
    first_bad_seq: int | None


def _field(value: str | None) -> bytes:
    if value is None:
        return b"\x00"
    raw = value.encode("utf-8")
    return b"\x01" + struct.pack(">q", len(raw)) + raw


def _int_field(value: int | None) -> bytes:
    return _field(None if value is None else str(int(value)))


def epoch_us(ts: datetime) -> int:
    """Microseconds since the Unix epoch; exact for Postgres timestamptz."""
    return (ts - _EPOCH) // _US


def canonical(row: Mapping[str, Any]) -> bytes:
    """Canonical bytes of one audit row without its hashes. `details_text` is details::text."""
    chain = list(row["act_chain"] or [])
    parts = [
        _VERSION,
        _int_field(row["seq"]),
        _int_field(epoch_us(row["ts"])),
        _field(row["actor"]),
        _int_field(len(chain)),
        *(_field(x) for x in chain),
        _field(row["event"]),
        _field(row["action"]),
        _field(row["resource"]),
        _field(row["decision"]),
        _field(row["reason"]),
        _field(row["route"]),
        _int_field(row["level"]),
        _field(row["approval_id"]),
        _field(row["run_id"]),
        _field(row["details_text"]),
    ]
    return b"".join(parts)


def row_hash(prev_hash: bytes, row: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(bytes(prev_hash) + canonical(row)).digest()


def _rows(conn: psycopg.Connection) -> Iterator[dict]:
    # Keyset pages instead of one big fetch: works on any connection (autocommit or not).
    last = None
    while True:
        with conn.cursor(row_factory=dict_row) as cur:
            if last is None:
                cur.execute(f"SELECT {_COLUMNS} FROM audit.log ORDER BY seq LIMIT %s", (_BATCH,))
            else:
                cur.execute(f"SELECT {_COLUMNS} FROM audit.log WHERE seq > %s ORDER BY seq LIMIT %s",
                            (last, _BATCH))
            page = cur.fetchall()
        if not page:
            return
        yield from page
        last = page[-1]["seq"]


def _sorted_anchors(anchors: Sequence[Anchor] | None) -> list[Anchor]:
    if anchors is None:
        anchors = read_anchors()  # raises AnchorError if the anchor file was tampered with
    # (seq, hash) order, identical to the SQL side's ORDER BY, so both report the same first_bad_seq
    return sorted(anchors, key=lambda a: (a.seq, bytes(a.hash)))


def _anchor_bad(anchors: list[Anchor], stored: Mapping[int, bytes]) -> int | None:
    """First seq the anchors can no longer vouch for: one past the last anchor that still matches
    (rows up to it are fixed by the chain links), or None when every anchor matches."""
    good = 0
    for a in anchors:
        if stored.get(a.seq) != bytes(a.hash):
            return good + 1
        good = a.seq
    return None


def verify_chain(conn: psycopg.Connection | None = None, *,
                 anchors: Sequence[Anchor] | None = None) -> ChainReport:
    """Recompute every hash in seq order and check the anchors. Uses an owner connection unless one
    is given; `anchors` defaults to the anchor file (pass [] to check the links only).

    A row is bad when its prev_hash is not the previous row's stored hash (first row: 32 zero
    bytes) or its hash does not match its content. An anchor is bad when its row is missing or its
    stored hash differs; then first_bad_seq is one past the last anchor that still matched.
    `checked` counts every row read.
    """
    anchor_list = _sorted_anchors(anchors)
    if conn is None:
        from corpai.db import pg

        with pg.connect_owner() as own:
            return verify_chain(own, anchors=anchor_list)
    wanted = {a.seq for a in anchor_list}
    stored_at: dict[int, bytes] = {}
    expected = ZERO_HASH
    checked = 0
    first_bad: int | None = None
    for row in _rows(conn):
        checked += 1
        prev, stored = bytes(row["prev_hash"]), bytes(row["hash"])
        if first_bad is None and (prev != expected or row_hash(prev, row) != stored):
            first_bad = row["seq"]
        if row["seq"] in wanted:
            stored_at[row["seq"]] = stored
        expected = stored
    bad_anchor = _anchor_bad(anchor_list, stored_at)
    if bad_anchor is not None:
        first_bad = bad_anchor if first_bad is None else min(first_bad, bad_anchor)
    return ChainReport(ok=first_bad is None, checked=checked, first_bad_seq=first_bad)


def verify_chain_sql(conn: psycopg.Connection | None = None, *,
                     anchors: Sequence[Anchor] | None = None) -> ChainReport:
    """The same check done by the database (audit.verify_chain(seqs, hashes)); tests compare the two."""
    anchor_list = _sorted_anchors(anchors)
    if conn is None:
        from corpai.db import pg

        with pg.connect_owner() as own:
            return verify_chain_sql(own, anchors=anchor_list)
    with conn.cursor(row_factory=dict_row) as cur:
        r = cur.execute("SELECT ok, checked, first_bad_seq FROM audit.verify_chain(%s::bigint[], %s::bytea[])",
                        ([a.seq for a in anchor_list], [bytes(a.hash) for a in anchor_list])).fetchone()
    return ChainReport(ok=bool(r["ok"]), checked=int(r["checked"]), first_bad_seq=r["first_bad_seq"])
