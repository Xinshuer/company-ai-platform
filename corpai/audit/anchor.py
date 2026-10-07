"""Anchors for the audit hash chain, kept outside the database.

The chain alone only proves that each row links to the one before it. Whoever controls the table
(its owner or a superuser, triggers disabled) can cut the tail, or rewrite a row and re-hash every
row after it, and the chain still verifies. An anchor is the head of the chain (seq, hash) written
to a file under the data directory, MAC'd with a key in the secrets directory; neither is reachable
through a database role. verify_chain() then also requires every anchored row to be present with
exactly the anchored hash, so a cut tail or a re-hashed history is reported.

What stays out of reach: rows appended after the newest anchor can still be dropped unnoticed, and
someone with write access to the data directory *and* the key can rewrite anchors too. Anchor
often (every owner job writes one) and, in a real deployment, ship anchors to append-only storage
the database host cannot write (docs/real-deployment.md).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corpai import clock

ANCHOR_FILE = "audit_anchors.jsonl"
KEY_FILE = "audit_anchor.key"

_lock = threading.Lock()


class AnchorError(RuntimeError):
    """The anchor file is malformed or an anchor's MAC does not verify (the file was edited)."""


@dataclass(frozen=True)
class Anchor:
    seq: int
    hash: bytes
    ts: str


def anchor_path() -> Path:
    from corpai.config import settings

    return settings().data_dir / ANCHOR_FILE


def _key_path() -> Path:
    from corpai.config import settings

    return settings().secrets_dir / KEY_FILE


def _key() -> bytes:
    path = _key_path()
    with _lock:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass  # another process created it first
            else:
                with os.fdopen(fd, "w", encoding="ascii", newline="") as f:
                    f.write(secrets.token_bytes(32).hex())
    try:
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
    except ValueError:
        raise AnchorError("audit anchor key is malformed") from None
    if len(key) < 32:
        raise AnchorError("audit anchor key is too short")
    return key


def _mac(key: bytes, seq: int, digest: bytes, ts: str) -> str:
    msg = f"corpai.audit.anchor.v1|{int(seq)}|{digest.hex()}|{ts}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def read_anchors(path: Path | None = None) -> list[Anchor]:
    """All anchors, oldest first. [] when none was written yet. Raises AnchorError on any line that
    does not parse or whose MAC is wrong: a tampered anchor file must not pass as "no anchors"."""
    path = path or anchor_path()
    if not path.exists():
        return []
    key = _key()
    out: list[Anchor] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj: dict[str, Any] = json.loads(line)
            seq, digest, ts, mac = int(obj["seq"]), bytes.fromhex(obj["hash"]), str(obj["ts"]), str(obj["mac"])
        except (ValueError, KeyError, TypeError):
            raise AnchorError(f"audit anchor file line {n} is malformed") from None
        if len(digest) != 32 or not hmac.compare_digest(mac, _mac(key, seq, digest, ts)):
            raise AnchorError(f"audit anchor file line {n} does not verify")
        out.append(Anchor(seq, digest, ts))
    return sorted(out, key=lambda a: a.seq)


def write_anchor(conn=None, *, path: Path | None = None) -> Anchor | None:
    """Append the current head of audit.log (owner connection) to the anchor file.

    Returns None when the log is empty. Anchoring does not re-verify the chain: an anchor only
    freezes what is there now, and older anchors still catch any rewrite of what they cover.
    """
    if conn is None:
        from corpai.db import pg

        with pg.connect_owner() as own:
            return write_anchor(own, path=path)
    with conn.cursor() as cur:
        row = cur.execute("SELECT seq, hash FROM audit.log ORDER BY seq DESC LIMIT 1").fetchone()
    if row is None:
        return None
    seq, digest = (row["seq"], bytes(row["hash"])) if isinstance(row, dict) else (row[0], bytes(row[1]))
    ts = clock.now_utc().isoformat()
    line = json.dumps({"seq": int(seq), "hash": digest.hex(), "ts": ts, "mac": _mac(_key(), seq, digest, ts)},
                      sort_keys=True)
    path = path or anchor_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    return Anchor(int(seq), digest, ts)
