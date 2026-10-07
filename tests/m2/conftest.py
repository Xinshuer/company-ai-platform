"""Fixtures for the M2 wiring tests (shared pg / seeded / personas / fake_llm / app_client come from
tests/conftest.py)."""

from __future__ import annotations

import json
from typing import Any

import pytest

import corpai.legal  # noqa: F401  registers the legal FakeLLM responders
from tests.api.support import login, owner_rows

APP = "legal_review"


async def legal_run(client: Any, request: dict | str, *, thread_id: str | None = None, user_level: int = 1) -> dict:
    """POST /api/runs for legal_review; `request` is the mode object (sent as JSON text) or plain text."""
    text = request if isinstance(request, str) else json.dumps(request)
    r = await client.post("/api/runs", json={"app": APP, "input": text, "thread_id": thread_id,
                                             "user_level": user_level})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def as_persona(app_client, personas):
    async def _login(key: str):
        await login(app_client, personas[key].user_id)
        return personas[key]
    return _login


@pytest.fixture(scope="session")
def contact_of(seeded):
    """account_id -> the first CRM contact e-mail of that account (owner read: test setup only)."""
    def _get(account_id: str) -> str:
        rows = owner_rows("SELECT email FROM crm.contacts WHERE account_id = %s ORDER BY contact_id LIMIT 1",
                          (account_id,))
        return rows[0]["email"]
    return _get


@pytest.fixture(scope="session")
def contracts(seeded) -> dict[str, dict]:
    """contract_id -> the stored legal.contracts row (owner read: test setup only)."""
    return {r["contract_id"]: dict(r) for r in owner_rows("SELECT * FROM legal.contracts ORDER BY contract_id")}


def proposals_of(user_id: str) -> list[dict]:
    return owner_rows("SELECT id, tool, tier, status, params FROM ops.proposals WHERE requester_id = %s"
                      " ORDER BY created_at", (user_id,))


def audit_of(run_id: str) -> list[dict]:
    return owner_rows("SELECT event, action, resource, decision, route, level FROM audit.log WHERE run_id = %s"
                      " ORDER BY seq", (run_id,))
