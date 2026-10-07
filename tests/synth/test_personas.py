"""People, personas, clearance and memberships (architecture §5.1 and §15)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import pytest

from corpai.db import pg as pgmod
from corpai.synth.personas import PERSONAS

EXPECTED = {  # key: (role, dept, clearance)
    "ceo": ("ceo", "exec", 4), "exec_staff": ("employee", "exec", 3),
    "sales_head": ("manager", "sales", 4), "sales_staff": ("employee", "sales", 3),
    "eng_head": ("manager", "eng", 4), "eng_staff": ("employee", "eng", 3),
    "legal_head": ("manager", "legal", 4), "legal_staff": ("employee", "legal", 4),
    "finance_head": ("manager", "finance", 4), "finance_staff": ("employee", "finance", 4),
    "hr_head": ("manager", "hr", 4), "hr_staff": ("employee", "hr", 4),
    "platform_admin": ("platform_admin", "it", 2), "auditor": ("security_auditor", "it", 2),
}


@pytest.fixture(scope="module")
def users(synth_small):
    with pgmod.connect_owner() as c:
        return {r["user_id"]: r for r in c.execute("SELECT * FROM core.users").fetchall()}


@pytest.fixture(scope="module")
def memberships(synth_small):
    with pgmod.connect_owner() as c:
        rows = c.execute("SELECT user_id, grp FROM core.memberships").fetchall()
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["user_id"], set()).add(r["grp"])
    return out


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_persona_row(users, key):
    u = users[PERSONAS[key]]
    assert (u["role"], u["dept"], u["clearance"]) == EXPECTED[key]
    assert u["persona_key"] == key and u["active"]
    assert u["email"].endswith("@kestrelbyte.example")


def test_department_sizes_and_service_accounts(users):
    humans = [u for u in users.values() if u["role"] != "service"]
    assert Counter(u["dept"] for u in humans) == {"exec": 2, "sales": 14, "eng": 32, "legal": 4, "finance": 6,
                                                  "hr": 5, "it": 3}
    svc = {u["user_id"]: u for u in users.values() if u["role"] == "service"}
    assert set(svc) == {"svc:ingest", "svc:metrics", "svc:eval", "svc:executor"}
    assert all(u["dept"] == "it" and u["clearance"] == 1 for u in svc.values())
    others = [u for u in humans if u["persona_key"] is None]
    assert all(int(u["user_id"][1:]) >= 100 for u in others)


def test_clearance_rule_for_everyone(users):
    for u in users.values():
        if u["role"] == "service":
            expected = 1
        elif u["dept"] == "it":
            expected = 2
        elif u["role"] in ("ceo", "manager") or u["dept"] in ("legal", "finance", "hr"):
            expected = 4
        else:
            expected = 3
        assert u["clearance"] == expected, u["user_id"]


def test_one_head_per_department_and_managers_exist(users):
    heads = Counter(u["dept"] for u in users.values() if u["role"] == "manager")
    assert heads == {"sales": 1, "eng": 1, "legal": 1, "finance": 1, "hr": 1}
    for u in users.values():
        if u["manager_id"]:
            assert u["manager_id"] in users


def test_persona_memberships(memberships):
    assert any(g.startswith("proj:") for g in memberships[PERSONAS["eng_staff"]])
    assert any(g.startswith("req:") for g in memberships[PERSONAS["hr_staff"]])
    assert "matter:M-01" in memberships[PERSONAS["legal_staff"]]
    # matters stay with their handlers: the CEO has no matter membership
    assert not any(g.startswith("matter:") for g in memberships.get(PERSONAS["ceo"], set()))


def test_sales_staff_owns_at_least_three_accounts(synth_small):
    with pgmod.connect_owner() as c:
        n = c.execute("SELECT count(*) AS n FROM crm.accounts WHERE owner_id = %s",
                      (PERSONAS["sales_staff"],)).fetchone()["n"]
    assert n >= 3


def test_legal_staff_handles_at_least_three_contracts_and_one_matter(synth_small):
    with pgmod.connect_owner() as c:
        n = c.execute("SELECT count(*) AS n FROM legal.contracts WHERE handler_id = %s",
                      (PERSONAS["legal_staff"],)).fetchone()["n"]
        m = c.execute("SELECT count(*) AS n FROM legal.matters WHERE handler_id = %s",
                      (PERSONAS["legal_staff"],)).fetchone()["n"]
    assert n >= 3 and m >= 1


def test_hiring_managers_and_project_members_get_groups(synth_small, memberships):
    with pgmod.connect_owner() as c:
        reqs = c.execute("SELECT req_id, hiring_manager_id, recruiter_id FROM hr.requisitions").fetchall()
        handlers = c.execute("SELECT matter_id, handler_id FROM legal.matters").fetchall()
    for r in reqs:
        assert f"req:{r['req_id']}" in memberships[r["hiring_manager_id"]]
        assert f"req:{r['req_id']}" in memberships[r["recruiter_id"]]
    for m in handlers:
        assert f"matter:{m['matter_id']}" in memberships[m["handler_id"]]


def test_legal_staff_crm_grant(synth_small):
    with pgmod.connect_owner() as c:
        rows = c.execute("SELECT * FROM core.grants").fetchall()
    assert len(rows) == 1
    g = rows[0]
    assert g["user_id"] == PERSONAS["legal_staff"]
    assert g["grp"] == "grant:crm:A-0003" and g["reason"] == "contract review ticket"
    # principal_row() compares with the real clock: the grant must outlive the simulated year
    assert g["expires_at"] == datetime(2027, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert g["revoked_at"] is None and not g["break_glass"]


def test_hr_employees_cover_every_human_and_no_pay_columns(synth_small, users):
    with pgmod.connect_owner() as c:
        emp = {r["employee_id"] for r in c.execute("SELECT employee_id FROM hr.employees").fetchall()}
        cols = {r["column_name"] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='hr' AND table_name='employees'")}
    assert emp == {u for u, r in users.items() if r["role"] != "service"}
    assert not cols & {"base_salary_eur", "salary", "rating", "bonus_target_pct"}
