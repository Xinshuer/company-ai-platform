from __future__ import annotations

import pytest

from tests.mcp.support import Caller, install_writer_agent, mcp_app
from tests.metrics.conftest import drafts, published  # noqa: F401 - metric snapshots for the finance tools


@pytest.fixture
async def app(seeded):
    async with mcp_app() as a:
        yield a


@pytest.fixture
async def call(app, personas):
    return Caller(app, personas)


@pytest.fixture
def writer_agent(monkeypatch):
    install_writer_agent(monkeypatch)
