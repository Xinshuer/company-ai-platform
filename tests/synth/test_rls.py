"""RLS on the generated data: fail-closed without a principal, owner-only eval schema, no write grants,
and what a few personas can and cannot see (design-review §5)."""

from __future__ import annotations

from dataclasses import replace

import psycopg
import pytest

from corpai.db import pg as pgmod
from corpai.synth.personas import PERSONAS
from corpai.synth.writer import BUSINESS_TABLES
from corpai.types import Level


def _ids(conn, sql: str) -> set:
    return {tuple(r.values())[0] for r in conn.execute(sql).fetchall()}


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def _owner(sql: str, *args) -> set:
    with pgmod.connect_owner() as c:
        return {tuple(r.values())[0] for r in c.execute(sql, args).fetchall()}


# ---- fail-closed and grants -----------------------------------------------------------------

@pytest.mark.parametrize("table", BUSINESS_TABLES)
def test_app_without_principal_sees_nothing(synth_small, table):
    with pgmod.connect_app() as conn:
        assert _count(conn, table) == 0


def test_tables_are_not_empty_for_the_owner(synth_small):
    with pgmod.connect_owner() as c:
        empty = [t for t in BUSINESS_TABLES if t != "hr.screenings" and _count(c, t) == 0]
    assert empty == []


@pytest.mark.parametrize("table", ["synth.planted", "synth.canaries"])
def test_eval_answers_are_invisible_to_the_app(synth_small, principal_for, table):
    ceo = principal_for(PERSONAS["ceo"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(ceo) as conn:
            conn.execute(f"SELECT * FROM {table}")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.connect_app() as conn:
            conn.execute(f"SELECT * FROM {table}")


def test_app_has_select_only(synth_small):
    with pgmod.connect_owner() as c:
        for t in BUSINESS_TABLES:
            row = c.execute("SELECT has_table_privilege('corpai_app', %s, 'SELECT') AS s, "
                            "has_table_privilege('corpai_app', %s, 'INSERT') AS i, "
                            "has_table_privilege('corpai_app', %s, 'UPDATE') AS u, "
                            "has_table_privilege('corpai_app', %s, 'DELETE') AS d, "
                            "has_table_privilege('corpai_app', %s, 'TRUNCATE') AS tr", (t, t, t, t, t)).fetchone()
            assert row == {"s": True, "i": False, "u": False, "d": False, "tr": False}, t
            rls = c.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
                            (t,)).fetchone()
            assert rls == {"relrowsecurity": True, "relforcerowsecurity": False}, t


def test_app_write_attempt_is_denied(synth_small, principal_for):
    ceo = principal_for(PERSONAS["ceo"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(ceo) as conn:
            conn.execute("UPDATE crm.accounts SET arr_eur = 0 WHERE account_id = 'A-0001'")


# ---- personas --------------------------------------------------------------------------------

def test_sales_staff_sees_only_own_accounts(synth_small, principal_for):
    uid = PERSONAS["sales_staff"]
    own = _owner("SELECT account_id FROM crm.accounts WHERE owner_id = %s", uid)
    assert len(own) >= 3
    with pgmod.principal_tx_sync(principal_for(uid)) as conn:
        assert _ids(conn, "SELECT account_id FROM crm.accounts") == own
        assert _ids(conn, "SELECT DISTINCT account_id FROM crm.contacts") == own
        assert _ids(conn, "SELECT DISTINCT account_id FROM crm.interactions") <= own
        assert _count(conn, "eng.projects") > 0  # stage-level project status is visible to sales
        for t in ("eng.tickets", "eng.ci_runs", "legal.contracts", "fin.invoices", "hr.employees"):
            assert _count(conn, t) == 0, t
        assert _ids(conn, "SELECT request_id FROM legal.contract_requests") == _owner(
            "SELECT request_id FROM legal.contract_requests WHERE requested_by = %s", uid)


def test_eng_staff_sees_no_crm_and_only_own_projects_work(synth_small, principal_for):
    uid = PERSONAS["eng_staff"]
    p = principal_for(uid)
    projects = {g.split(":", 1)[1] for g in p.groups if g.startswith("proj:")}
    assert projects
    with pgmod.principal_tx_sync(p) as conn:
        for t in ("crm.accounts", "crm.contacts", "crm.opportunities", "crm.interactions", "crm.quotes"):
            assert _count(conn, t) == 0, t
        assert _ids(conn, "SELECT DISTINCT project_id FROM eng.tickets") == projects
        assert _ids(conn, "SELECT DISTINCT project_id FROM eng.ci_runs") == projects
        assert _count(conn, "eng.vulnerabilities") == 0  # L4, CTO and CEO only
        assert _count(conn, "eng.projects") == _count_owner("eng.projects")


def _count_owner(table: str) -> int:
    with pgmod.connect_owner() as c:
        return _count(c, table)


def test_hr_staff_sees_no_pay_or_ratings(synth_small, principal_for):
    p = principal_for(PERSONAS["hr_staff"])
    reqs = {g.split(":", 1)[1] for g in p.groups if g.startswith("req:")}
    with pgmod.principal_tx_sync(p) as conn:
        assert _count(conn, "hr.compensation") == 0
        assert _count(conn, "hr.performance") == 0
        assert _count(conn, "hr.requisition_budgets") == 0
        assert _count(conn, "hr.employees") == _count_owner("hr.employees")
        assert _ids(conn, "SELECT DISTINCT req_id FROM hr.candidates") == reqs
    with pgmod.principal_tx_sync(principal_for(PERSONAS["hr_head"])) as conn:
        assert _count(conn, "hr.compensation") == _count_owner("hr.compensation")


def test_ceo_sees_no_matters_without_a_grant(synth_small, principal_for):
    ceo_id = PERSONAS["ceo"]
    with pgmod.principal_tx_sync(principal_for(ceo_id)) as conn:
        assert _count(conn, "legal.matters") == 0
        assert _count(conn, "hr.compensation") == 0  # head:hr only, not the CEO
        assert _count(conn, "eng.vulnerabilities") > 0
        assert _count(conn, "crm.accounts") == _count_owner("crm.accounts")
    with pgmod.connect_owner() as c:
        c.execute("INSERT INTO core.grants (grant_id, user_id, grp, reason, granted_by, created_at, expires_at) VALUES "
                  "('T-OLD', %s, 'grant:matter:M-02', 'expired test grant', 'u0030', '2026-01-01', '2026-02-01'),"
                  "('T-NEW', %s, 'grant:matter:M-01', 'board briefing', 'u0030', '2026-09-30', '2099-01-01')",
                  (ceo_id, ceo_id))
    try:
        with pgmod.principal_tx_sync(principal_for(ceo_id)) as conn:
            assert _ids(conn, "SELECT matter_id FROM legal.matters") == {"M-01"}  # expired grant gives nothing
    finally:
        with pgmod.connect_owner() as c:
            c.execute("DELETE FROM core.grants WHERE grant_id IN ('T-OLD', 'T-NEW')")


def test_ceo_matter_grant_agrees_between_rls_and_decide(synth_small, principal_for):
    """design-review 五, 诉讼: CEO 需单独授权. The grant is a real core.grants row named
    grant:matter:<id> (like grant:crm:<account>); it must open the real seeded row in RLS *and* in
    decide(), for that matter only."""
    from corpai.policy import decide
    from corpai.types import Action, Dept, Label, Resource, Trust

    ceo_id = PERSONAS["ceo"]
    with pgmod.connect_owner() as c:
        c.execute("INSERT INTO core.grants (grant_id, user_id, grp, reason, granted_by, created_at, expires_at) "
                  "VALUES ('T-MATTER', %s, 'grant:matter:M-01', 'board briefing', 'u0030', '2026-09-30', "
                  "'2099-01-01')", (ceo_id,))
        rows = {r["matter_id"]: r for r in c.execute("SELECT * FROM legal.matters").fetchall()}
    try:
        p = principal_for(ceo_id)
        assert "grant:matter:M-01" in p.groups
        with pgmod.principal_tx_sync(p) as conn:
            visible = _ids(conn, "SELECT matter_id FROM legal.matters")
        allowed = {m for m, r in rows.items()
                   if decide(p, Action.READ, Resource("legal.matter", m, Label(
                       level=Level(r["level"]), dept=Dept(r["dept"]), acl_groups=frozenset(r["acl_groups"]),
                       trust=Trust(r["trust"])))).allow}
        assert visible == allowed == {"M-01"}
    finally:
        with pgmod.connect_owner() as c:
            c.execute("DELETE FROM core.grants WHERE grant_id = 'T-MATTER'")


def test_legal_staff_crm_access_only_through_the_grant(synth_small, principal_for):
    p = principal_for(PERSONAS["legal_staff"])
    assert "grant:crm:A-0003" in p.groups  # the synth grant is live (it must not expire with the calendar)
    with pgmod.principal_tx_sync(p) as conn:
        assert _ids(conn, "SELECT account_id FROM crm.accounts") == {"A-0003"}
        assert _ids(conn, "SELECT matter_id FROM legal.matters") == {"M-01"}
        assert _ids(conn, "SELECT contract_id FROM legal.contracts") == _owner(
            "SELECT contract_id FROM legal.contracts WHERE handler_id = %s", p.user_id)
    without = replace(p, groups=frozenset(g for g in p.groups if not g.startswith("grant:")))
    with pgmod.principal_tx_sync(without) as conn:
        assert _count(conn, "crm.accounts") == 0


def test_manager_sees_only_direct_reports_profiles(synth_small, principal_for):
    lead = principal_for("u0112")  # an engineering lead, clearance L3
    reports = {g.split(":", 1)[1] for g in lead.groups if g.startswith("mgr-of:")}
    assert reports
    with pgmod.principal_tx_sync(lead) as conn:
        assert _ids(conn, "SELECT employee_id FROM hr.employees") == reports
        assert _count(conn, "hr.compensation") == 0


def test_it_roles_read_no_business_content(synth_small, principal_for):
    for key in ("platform_admin", "auditor"):
        p = principal_for(PERSONAS[key])
        with pgmod.principal_tx_sync(p) as conn:
            visible = {t: _count(conn, t) for t in BUSINESS_TABLES}
        # only the public GDPR library (L1, all) is readable
        assert {t for t, n in visible.items() if n} == {"legal.regulations"}, key


def test_finance_staff_sees_ledger_but_not_crm_or_hiring_budgets(synth_small, principal_for):
    with pgmod.principal_tx_sync(principal_for(PERSONAS["finance_staff"])) as conn:
        assert _count(conn, "fin.journal_entries") == _count_owner("fin.journal_entries")
        assert _count(conn, "fin.invoices") == _count_owner("fin.invoices")
        assert _count(conn, "crm.accounts") == 0
        assert _count(conn, "hr.requisition_budgets") == 0


def test_forged_groups_do_not_beat_the_clearance_ceiling(synth_small, principal_for):
    p = principal_for(PERSONAS["sales_staff"])
    forged = replace(p, groups=p.groups | {"head:hr", "role:ceo", "head:legal", "head:eng", "matter:M-01"})
    with pgmod.principal_tx_sync(forged) as conn:  # clearance L3: L4 rows stay hidden whatever the groups
        for t in ("hr.compensation", "hr.performance", "legal.matters", "eng.vulnerabilities"):
            assert _count(conn, t) == 0, t


def test_deactivated_principal_sees_nothing(synth_small, principal_for):
    p = replace(principal_for(PERSONAS["ceo"]), active=False)
    with pgmod.principal_tx_sync(p) as conn:
        assert all(_count(conn, t) == 0 for t in BUSINESS_TABLES)


def test_service_account_sees_only_public_rows(synth_small, principal_for):
    svc = principal_for("svc:ingest")
    assert svc.clearance == Level.L1 and "all" not in svc.groups
    with pgmod.principal_tx_sync(svc) as conn:
        assert all(_count(conn, t) == 0 for t in BUSINESS_TABLES)
