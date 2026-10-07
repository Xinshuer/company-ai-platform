from __future__ import annotations

import pytest

from tests.api.support import login


@pytest.fixture
def as_persona(app_client, personas):
    """async as_persona("sales_staff") -> logs the client in as that persona and returns its Principal."""
    async def _login(key: str):
        await login(app_client, personas[key].user_id)
        return personas[key]
    return _login
