"""Fixtures for the classifier tests: a fixed gazetteer, restored afterwards."""

from __future__ import annotations

import pytest

from corpai.classify import names

GAZETTEER = ["Anna Becker", "Jonas Weber", "Lukas Schmidt", "Marie Dubois", "Piotr Nowak", "Daniel Sjögren", "王伟"]


@pytest.fixture(autouse=True)
def test_gazetteer():
    saved = names._state
    names.set_gazetteer(GAZETTEER)
    yield GAZETTEER
    names._state = saved
