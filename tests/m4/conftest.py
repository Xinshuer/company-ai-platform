"""Fixtures for the M4 wiring tests (sales_profile, hr_* graphs, their routes and pages).

The API tests run end to end: the ASGI app with its lifespan (gazetteer loaded, so public pages can
route external), real MCP servers, the FakeLLM. tests/security/test_permission_matrix_rls.py expects
hr.screenings to be empty in the seed, so every screening row written here is removed again.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.api.support import login, owner_exec, owner_rows


@pytest.fixture(scope="module", autouse=True)
def _clean_screenings(seeded):
    yield
    owner_exec("DELETE FROM hr.screenings")
    owner_exec("DELETE FROM hr.screening_evidence")


@pytest.fixture(autouse=True)
def _switches_off(seeded):
    owner_exec("UPDATE ops.switches SET \"on\" = false WHERE name IN ('local_down', 'external_down')")
    yield
    owner_exec("UPDATE ops.switches SET \"on\" = false WHERE name IN ('local_down', 'external_down')")


@pytest.fixture(autouse=True)
def _fake_responders():
    """Tests that simulate a compromised model override a purpose; restore the honest ones afterwards."""
    from corpai.hr import register_fake_responders as hr_fakes
    from corpai.sales import register_fake_responders as sales_fakes

    sales_fakes()
    hr_fakes()
    yield
    sales_fakes()
    hr_fakes()


@pytest.fixture
def as_persona(app_client, personas):
    async def _login(key: str):
        await login(app_client, personas[key].user_id)
        return personas[key]
    return _login


@pytest.fixture
def clone_candidate(seeded):
    """clone_candidate(source_id) -> id of a temporary copy (owner write). Decisions and their audit rows
    then belong to a candidate no other test looks at (audit rows are append-only)."""
    from corpai.ids import new_id

    made: list[str] = []

    def _clone(source_id: str) -> str:
        cid = f"CAND-TM4{new_id('x')[-8:]}"
        owner_exec(
            "INSERT INTO hr.candidates (candidate_id, req_id, name, email, phone, resume_text, source, applied_on, "
            "stage, level, dept, owner_id, acl_groups, trust, subject_ids) "
            "SELECT %s, req_id, name, email, phone, resume_text, source, applied_on, stage, level, dept, owner_id, "
            "acl_groups, trust, ARRAY[%s] FROM hr.candidates WHERE candidate_id = %s", (cid, cid, source_id))
        made.append(cid)
        return cid

    yield _clone
    for cid in made:
        owner_exec("DELETE FROM hr.screenings WHERE candidate_id = %s", (cid,))
        owner_exec("DELETE FROM hr.screening_evidence WHERE candidate_id = %s", (cid,))
        owner_exec("DELETE FROM hr.candidates WHERE candidate_id = %s", (cid,))


async def post_run(client: Any, text: str, *, app: str, thread_id: str | None = None, user_level: int = 1) -> dict:
    r = await client.post("/api/runs", json={"app": app, "input": text, "thread_id": thread_id,
                                             "user_level": user_level})
    assert r.status_code == 200, r.text
    return r.json()


def keys_of(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in keys_of(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in keys_of(v)}
    return set()


def audit_rows(run_id: str) -> list[dict]:
    return owner_rows("SELECT * FROM audit.log WHERE run_id = %s ORDER BY seq", (run_id,))
