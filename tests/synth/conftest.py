"""Fixtures for the synth tests. They call synth.run directly (not the shared `seeded` fixture) so the
generator is tested on its own. Principals come from identity (corpai.identity.directory), the
groups RLS really receives; test_contract.py keeps a hand-written §5 derivation as a contract
test that must equal identity's."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

import pytest

from corpai.config import settings
from corpai.db import pg as pgmod
from corpai.types import Principal


@pytest.fixture(scope="session")
def synth_small(pg):
    """The small seed-7 company. Tests that regenerate something else restore this state when done."""
    from corpai import synth

    return synth.run(seed=7, small=True)


@pytest.fixture
def principal_for(synth_small) -> Callable[..., Principal]:
    """The Principal identity resolves for a user: the groups RLS actually receives."""
    from dataclasses import replace

    from corpai.identity.directory import get_principal_sync

    def _get(user_id: str, *, extra_groups: frozenset[str] = frozenset()) -> Principal:
        p = get_principal_sync(user_id)
        assert p is not None, user_id
        return replace(p, groups=p.groups | extra_groups) if extra_groups else p
    return _get


def table_checksums(tables) -> dict[str, str]:
    out = {}
    with pgmod.connect_owner() as conn:
        for t in tables:
            row = conn.execute(f"SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) "
                               f"AS h FROM {t} x").fetchone()
            out[t] = f"{row['n']}:{row['h']}"
    return out


def corpus_hashes() -> dict[str, str]:
    root: Path = settings().corpus_dir
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def checksums():
    return table_checksums


@pytest.fixture
def corpus_digest():
    return corpus_hashes
