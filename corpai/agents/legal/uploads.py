"""Uploaded contract files, held until a review run picks them up (POST /api/legal/uploads).

A run's input is limited to a few thousand characters (POST /api/runs), while a real contract is
longer. The UI therefore uploads the file first and starts the review with the returned upload id.

Kept in process memory, per user, for a short time: an upload is untrusted counterparty text that
only the uploader's own review run may read (the intake node looks it up with the principal of its
RunContext, never with an id from the model), and it is not a record the platform has to keep.
`python -m corpai serve` is one process; a deployment with several workers would put this in the
database with the same owner rule (docs/real-deployment.md).
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass

from corpai.ids import new_id

MAX_CHARS = 200_000  # corpai.legal.review.MAX_TEXT_CHARS
MAX_PER_USER = 10
TTL_SECONDS = 2 * 3600


@dataclass(frozen=True)
class Upload:
    upload_id: str
    owner_id: str
    text: str
    filename: str
    created: float

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


_lock = threading.Lock()
_store: dict[str, Upload] = {}


def _prune(now: float) -> None:
    for uid in [k for k, u in _store.items() if now - u.created > TTL_SECONDS]:
        _store.pop(uid, None)


def put(owner_id: str, text: str, filename: str = "") -> Upload:
    """Keep `text` for `owner_id`. ValueError for empty or oversized text. The oldest upload of the
    same user is dropped beyond MAX_PER_USER, so one user cannot fill the process memory."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the file is empty")
    if len(text) > MAX_CHARS:
        raise ValueError(f"the contract is too long (max {MAX_CHARS} characters)")
    name = filename if isinstance(filename, str) else ""
    name = "".join(ch for ch in name if ch.isprintable())[:120]
    now = time.time()
    up = Upload(upload_id=new_id("upl"), owner_id=owner_id, text=text, filename=name, created=now)
    with _lock:
        _prune(now)
        mine = sorted((u for u in _store.values() if u.owner_id == owner_id), key=lambda u: u.created)
        for old in mine[: max(0, len(mine) - MAX_PER_USER + 1)]:
            _store.pop(old.upload_id, None)
        _store[up.upload_id] = up
    return up


def get(owner_id: str, upload_id: str) -> Upload | None:
    """The upload if it exists, has not expired and belongs to `owner_id`; otherwise None (someone
    else's upload and a missing one look the same)."""
    with _lock:
        _prune(time.time())
        up = _store.get(upload_id) if isinstance(upload_id, str) else None
    return up if up is not None and up.owner_id == owner_id else None


def clear() -> None:
    """Tests only."""
    with _lock:
        _store.clear()
