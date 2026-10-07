"""HMAC keys for session, tool and gateway tokens, one file each under settings().secrets_dir.

Separate keys so a leak of one kind cannot mint another. A key is 32 random bytes stored as hex,
created on first use with mode 0600 (best effort on Windows). Deleting a key file invalidates
every token of that kind, which is the emergency "log everybody out" lever. Keys are read on each
use (a tiny file) so that lever works without a restart.
"""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from corpai.config import settings

SESSION = "session.key"
TOOL = "tool.key"
GATEWAY = "gateway.key"

_lock = threading.Lock()


class KeyFileError(RuntimeError):
    pass


def _path(name: str) -> Path:
    return settings().secrets_dir / name


def _create(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return  # another process won the race; use its key
    with os.fdopen(fd, "w", encoding="ascii", newline="") as f:
        f.write(secrets.token_bytes(32).hex())


def load_key(name: str) -> bytes:
    """The raw key bytes; created on first use. Fails closed on a malformed file."""
    path = _path(name)
    with _lock:
        if not path.exists():
            _create(path)
    text = path.read_text(encoding="ascii").strip()
    try:
        key = bytes.fromhex(text)
    except ValueError:
        raise KeyFileError(f"key file {name} is malformed; delete it to rotate") from None
    if len(key) < 32:
        raise KeyFileError(f"key file {name} is too short; delete it to rotate")
    return key
