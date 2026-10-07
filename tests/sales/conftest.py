"""Fixtures for the M4 sales tests: seeded company, loaded gazetteer, switches off, web corpus."""

from __future__ import annotations

import pytest

from corpai.db import pg as pgmod
from corpai.ids import new_id


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    with pgmod.connect_owner() as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture(autouse=True)
def _gazetteer(seeded):
    """As in the API: person names known from the directory, CRM contacts and candidates. Without it
    the gateway fails closed (>= L3) and no web extraction could ever route external."""
    from corpai.classify import load_gazetteer_from_db, names

    saved = names._state
    load_gazetteer_from_db()
    yield
    names._state = saved


@pytest.fixture(autouse=True)
def _switches_off(seeded):
    def _set() -> None:
        with pgmod.connect_owner() as c:
            c.execute("UPDATE ops.switches SET \"on\" = false WHERE name IN ('local_down', 'external_down')")
    _set()
    yield
    _set()


@pytest.fixture
def run_id() -> str:
    return new_id("run")


@pytest.fixture(scope="session")
def web_pages(seeded) -> list[dict]:
    from corpai.sales import load_web_pages

    return load_web_pages()


@pytest.fixture(autouse=True)
def _fake_responders():
    """Tests that simulate a compromised model override 'sales.web_extract'; restore it afterwards."""
    from corpai.sales import register_fake_responders

    register_fake_responders()
    yield
    register_fake_responders()
