"""Keep the API process's person-name gazetteer in step with the database (architecture §7, §12).

The gazetteer is process-global and loaded by the owner-side loader. `python -m corpai synth` and
`ingest` run in other processes, so their reload never reaches a running API: after a re-synth with
another seed, the API would keep the old, non-empty name list, the gateway's `gazetteer_unloaded`
fail-safe would not trigger, and the new contacts' and candidates' names would classify as L1
(issues-log R-01 / D-09).

Before every run the runner compares ops.name_source_fingerprint() (a digest of every source name,
migration 100) with the digest the loaded gazetteer was built from, and reloads when they differ.
If the reload or the check fails, the gazetteer is emptied, so the gateway fails closed (every
request at least L3, local) until a later check succeeds.
"""

from __future__ import annotations

import asyncio
import logging

import anyio

from corpai.db import pg

log = logging.getLogger(__name__)

_loaded_fp: str | None = None
_lock: asyncio.Lock | None = None
_lock_loop: object | None = None


def _get_lock() -> asyncio.Lock:
    # one lock per event loop (tests run several loops in one process)
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


async def _fingerprint() -> str:
    async with pg.app_tx() as conn:
        cur = await conn.execute("SELECT ops.name_source_fingerprint() AS fp")
        row = await cur.fetchone()
    return str(row["fp"])


def _fail_closed(why: str) -> None:
    global _loaded_fp
    from corpai.classify import set_gazetteer

    set_gazetteer([])
    _loaded_fp = None
    log.warning("person-name gazetteer emptied (%s); model calls stay local until it reloads", why)


async def ensure_fresh(*, force: bool = False) -> None:
    """Reload the gazetteer when the names in the database changed since it was loaded."""
    global _loaded_fp
    from corpai.classify import GazetteerError, load_gazetteer_from_db

    try:
        fp = await _fingerprint()
    except Exception as e:  # noqa: BLE001 - cannot tell whether the names changed: fail closed
        _fail_closed(type(e).__name__)
        return
    if fp == _loaded_fp and not force:
        return
    async with _get_lock():
        if fp == _loaded_fp and not force:
            return
        try:
            n = await anyio.to_thread.run_sync(load_gazetteer_from_db)
        except GazetteerError as e:
            _fail_closed(type(e).__name__)
            return
        # The digest read before loading: if the names change in between, the next check differs
        # again and reloads, so the gazetteer is never newer-looking than it is.
        _loaded_fp = fp
        log.info("person-name gazetteer loaded: %d names", n)


def loaded_fingerprint() -> str | None:
    return _loaded_fp
