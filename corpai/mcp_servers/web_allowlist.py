"""Which hosts web.fetch may open, and the mock web corpus behind web.search / web.fetch.

The mock corpus (settings().corpus_dir/web/pages.jsonl) is written by synth, a trusted owner job;
the allowlist is the set of hosts of its page URLs, restricted to the synthetic *.example TLD.
A URL a page merely *mentions* (an attacker address inside an injected page) is never a page
host, so it is not on the list. The egress inventory lists the whole thing as search.mock
(in_process, max L1): no request leaves the process.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.parse import urlsplit

from corpai.config import settings

SUFFIX = ".example"
_lock = threading.Lock()
_cache: dict[Path, tuple[float, list[dict]]] = {}


def pages_path() -> Path:
    return settings().corpus_dir / "web" / "pages.jsonl"


def load_pages() -> list[dict]:
    """All mock pages (cached until the file changes). Missing file → no pages (fail closed)."""
    path = pages_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        pages = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    pages.append(json.loads(line))
        _cache[path] = (mtime, pages)
        return pages


def host_of(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port:
        return None
    return parts.hostname.lower()


def allowed_hosts() -> frozenset[str]:
    hosts = set()
    for p in load_pages():
        h = host_of(str(p.get("url", "")))
        if h and h.endswith(SUFFIX):
            hosts.add(h)
    return frozenset(hosts)


def is_allowed(url: str) -> bool:
    h = host_of(url)
    return h is not None and h.endswith(SUFFIX) and h in allowed_hosts()
