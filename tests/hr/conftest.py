"""Fixtures for the M4 HR tests: seeded company, switches off, fake responders, clean-up of screenings.

tests/security/test_permission_matrix_rls.py expects hr.screenings to be empty in the seed, so every
row these tests write is removed again when the module finishes.
"""

from __future__ import annotations

import pytest

from corpai.db import pg as pgmod
from corpai.ids import new_id


def owner_rows(sql: str, params: tuple | None = None) -> list[dict]:
    with pgmod.connect_owner() as c:
        return c.execute(sql, params).fetchall()


def owner_exec(sql: str, params: tuple | None = None) -> None:
    with pgmod.connect_owner() as c:
        c.execute(sql, params)


@pytest.fixture(scope="module", autouse=True)
def _clean_screenings(seeded):
    yield
    owner_exec("DELETE FROM hr.screenings")
    owner_exec("DELETE FROM hr.screening_evidence")
    owner_exec("DELETE FROM hr.candidates WHERE candidate_id LIKE 'CAND-T%'")


@pytest.fixture(autouse=True)
def _switches_off(seeded):
    owner_exec("UPDATE ops.switches SET \"on\" = false WHERE name IN ('local_down', 'external_down')")
    yield


@pytest.fixture(autouse=True)
def _fake_responders():
    from corpai.hr import register_fake_responders

    register_fake_responders()
    yield
    register_fake_responders()


@pytest.fixture
def run_id() -> str:
    return new_id("run")


@pytest.fixture(scope="session")
def planted_pairs(seeded) -> list[dict]:
    (row,) = owner_rows("SELECT expected FROM synth.planted WHERE kind = 'counterfactual_pairs'")
    return row["expected"]["pairs"]


@pytest.fixture(scope="session")
def injected_candidate(seeded) -> str:
    (row,) = owner_rows("SELECT expected FROM synth.planted WHERE kind = 'injected_resume'")
    return row["expected"]["candidate_id"]


@pytest.fixture
def candidate_of(seeded):
    """candidate_of(req_id) -> a candidate id on that requisition."""
    def _pick(req_id: str) -> str:
        return owner_rows("SELECT candidate_id FROM hr.candidates WHERE req_id = %s AND candidate_id NOT LIKE "
                          "'CAND-T%%' ORDER BY candidate_id LIMIT 1", (req_id,))[0]["candidate_id"]
    return _pick


@pytest.fixture
def clone_candidate(seeded):
    """clone_candidate(source_id, resume_text) -> id of a temporary copy with another résumé (owner write)."""
    made: list[str] = []

    def _clone(source_id: str, resume_text: str) -> str:
        cid = f"CAND-T{len(made) + 1:02d}{new_id('x')[-6:]}"
        owner_exec(
            "INSERT INTO hr.candidates (candidate_id, req_id, name, email, phone, resume_text, source, applied_on, "
            "stage, level, dept, owner_id, acl_groups, trust, subject_ids) "
            "SELECT %s, req_id, name, email, phone, %s, source, applied_on, stage, level, dept, owner_id, acl_groups, "
            "trust, ARRAY[%s] FROM hr.candidates WHERE candidate_id = %s", (cid, resume_text, cid, source_id))
        made.append(cid)
        return cid

    yield _clone
    for cid in made:
        owner_exec("DELETE FROM hr.screenings WHERE candidate_id = %s", (cid,))
        owner_exec("DELETE FROM hr.screening_evidence WHERE candidate_id = %s", (cid,))
        owner_exec("DELETE FROM hr.candidates WHERE candidate_id = %s", (cid,))


@pytest.fixture(scope="session")
def principal_of(seeded):
    """principal_of(user_id) -> Principal from the directory (non-persona users such as hiring managers)."""
    from corpai.identity import directory

    def _get(user_id: str):
        p = directory.get_principal_sync(user_id)
        assert p is not None, user_id
        return p
    return _get
