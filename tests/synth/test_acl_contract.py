"""The §5.1 ACL contract, checked table by table against the written rows.

Expected groups are spelled out here on purpose (not imported from corpai.synth.labels), so a
change to the generator's mapping cannot silently change what the test accepts.
"""

from __future__ import annotations

import pytest

from corpai.db import pg as pgmod
from corpai.synth.writer import BUSINESS_TABLES


def _rows(sql: str) -> list[dict]:
    with pgmod.connect_owner() as c:
        return c.execute(sql).fetchall()


def _crm(owner: str, account: str) -> set[str]:
    return {f"user:{owner}", "head:sales", "role:ceo", f"grant:crm:{account}"}


def _lvl(r: dict, level: int) -> int:
    """§5.1: a row that hosts a canary is L4, whatever its kind's level; its ACL stays the kind's."""
    return 4 if "KB-CANARY-" in " ".join(str(v) for v in r.values()) else level


CASES = {
    "crm.accounts": ("SELECT *, owner_id AS acc_owner FROM crm.accounts",
                     lambda r: (3, _crm(r["acc_owner"], r["account_id"]))),
    "crm.contacts": ("SELECT x.*, a.owner_id AS acc_owner FROM crm.contacts x JOIN crm.accounts a USING (account_id)",
                     lambda r: (3, _crm(r["acc_owner"], r["account_id"]))),
    "crm.opportunities": ("SELECT x.*, a.owner_id AS acc_owner FROM crm.opportunities x "
                          "JOIN crm.accounts a USING (account_id)",
                          lambda r: (3, _crm(r["acc_owner"], r["account_id"]))),
    "crm.interactions": ("SELECT x.*, a.owner_id AS acc_owner FROM crm.interactions x "
                         "JOIN crm.accounts a USING (account_id)",
                         lambda r: (_lvl(r, 3), _crm(r["acc_owner"], r["account_id"]))),
    "crm.quotes": ("SELECT x.*, a.owner_id AS acc_owner FROM crm.quotes x JOIN crm.accounts a USING (account_id)",
                   lambda r: (3, _crm(r["acc_owner"], r["account_id"]))),
    "eng.projects": ("SELECT * FROM eng.projects",
                     lambda r: (3, {f"proj:{r['project_id']}", "dept:eng", "head:eng", "dept:sales", "role:ceo"})),
    "eng.tickets": ("SELECT * FROM eng.tickets", lambda r: (3, {f"proj:{r['project_id']}", "head:eng"})),
    "eng.ci_runs": ("SELECT * FROM eng.ci_runs", lambda r: (3, {f"proj:{r['project_id']}", "head:eng"})),
    "eng.vulnerabilities": ("SELECT * FROM eng.vulnerabilities", lambda r: (4, {"head:eng", "role:ceo"})),
    "legal.contracts": ("SELECT * FROM legal.contracts",
                        lambda r: (_lvl(r, 3), {f"user:{r['handler_id']}", "head:legal", "role:ceo"})),
    "legal.contract_requests": ("SELECT * FROM legal.contract_requests",
                                lambda r: (3, {f"user:{r['requested_by']}", "head:sales", "dept:legal", "head:legal"})),
    # the CEO reads a matter only through a core.grants row grant:matter:<id> (design-review 五: 需单独授权)
    "legal.matters": ("SELECT * FROM legal.matters",
                      lambda r: (4, {f"matter:{r['matter_id']}", "head:legal", f"grant:matter:{r['matter_id']}"})),
    "legal.regulations": ("SELECT * FROM legal.regulations", lambda r: (1, {"all"})),
    "legal.clause_library": ("SELECT * FROM legal.clause_library",
                             lambda r: (3, {"dept:legal", "head:legal", "role:ceo"})),
    "fin.chart_of_accounts": ("SELECT * FROM fin.chart_of_accounts",
                              lambda r: (2, {"dept:finance", "head:finance", "role:ceo"})),
    "fin.journal_entries": ("SELECT * FROM fin.journal_entries",
                            lambda r: (3, {"dept:finance", "head:finance", "role:ceo"})),
    "fin.invoices": ("SELECT * FROM fin.invoices", lambda r: (3, {"dept:finance", "head:finance", "role:ceo"})),
    "fin.budgets": ("SELECT * FROM fin.budgets", lambda r: (3, {"dept:finance", "head:finance", "role:ceo"})),
    "hr.employees": ("SELECT * FROM hr.employees",
                     lambda r: (3, {"dept:hr", "head:hr", f"mgr-of:{r['employee_id']}"})),
    "hr.compensation": ("SELECT * FROM hr.compensation", lambda r: (4, {"head:hr"})),
    "hr.performance": ("SELECT * FROM hr.performance", lambda r: (4, {"head:hr"})),
    "hr.requisitions": ("SELECT * FROM hr.requisitions",
                        lambda r: (2, {"dept:hr", "head:hr", f"head:{r['dept']}", "role:ceo", f"req:{r['req_id']}"})),
    "hr.requisition_budgets": ("SELECT x.*, q.dept AS hiring_dept FROM hr.requisition_budgets x "
                               "JOIN hr.requisitions q USING (req_id)",
                               lambda r: (3, {"head:hr", f"head:{r['hiring_dept']}", "head:finance", "role:ceo"})),
    "hr.candidates": ("SELECT * FROM hr.candidates", lambda r: (3, {f"req:{r['req_id']}", "head:hr"})),
    "hr.screenings": ("SELECT * FROM hr.screenings", lambda r: (4, {f"req:{r['req_id']}", "head:hr"})),
}


def test_every_business_table_has_a_case():
    assert set(CASES) == set(BUSINESS_TABLES)


@pytest.mark.parametrize("table", sorted(CASES))
def test_acl_contract(synth_small, table):
    sql, expect = CASES[table]
    rows = _rows(sql)
    if table != "hr.screenings":  # created empty; filled later by the HR screening executor
        assert rows, f"{table} is empty"
    for r in rows:
        level, acl = expect(r)
        assert r["level"] == level, (table, r)
        assert set(r["acl_groups"]) == acl, (table, r["acl_groups"], acl)
        assert len(r["acl_groups"]) == len(set(r["acl_groups"]))
        if level >= 3:
            assert "all" not in r["acl_groups"]


def test_canary_hosts_are_l4(synth_small):
    hosts = _rows("SELECT 'crm.interactions' AS t, level FROM crm.interactions WHERE summary LIKE '%KB-CANARY-%' "
                  "UNION ALL SELECT 'legal.contracts', level FROM legal.contracts WHERE text LIKE '%KB-CANARY-%' "
                  "UNION ALL SELECT 'hr.performance', level FROM hr.performance WHERE summary LIKE '%KB-CANARY-%'")
    assert {h["t"] for h in hosts} == {"crm.interactions", "legal.contracts", "hr.performance"}
    assert {h["level"] for h in hosts} == {4}


@pytest.mark.parametrize("table,col", [("crm.contacts", "contact_id"), ("crm.interactions", "contact_id"),
                                       ("hr.employees", "employee_id"), ("hr.compensation", "employee_id"),
                                       ("hr.performance", "employee_id"), ("hr.candidates", "candidate_id")])
def test_subject_ids_name_the_people_a_row_is_about(synth_small, table, col):
    for r in _rows(f"SELECT {col} AS who, subject_ids FROM {table}"):
        assert r["subject_ids"] == [r["who"]]


def test_trust_levels(synth_small):
    assert {r["trust"] for r in _rows("SELECT trust FROM hr.candidates")} == {"untrusted"}
    assert {r["trust"] for r in _rows("SELECT trust FROM legal.regulations")} == {"reviewed"}
    contracts = _rows("SELECT status, trust FROM legal.contracts")
    for c in contracts:  # counterparty paper still in negotiation is untrusted input
        assert c["trust"] == ("untrusted" if c["status"] in ("draft", "negotiation") else "internal")


def test_owner_columns_match_the_business_owner(synth_small):
    for r in _rows("SELECT x.owner_id, a.owner_id AS acc_owner FROM crm.interactions x JOIN crm.accounts a "
                   "USING (account_id)"):
        assert r["owner_id"] == r["acc_owner"]
    for r in _rows("SELECT owner_id, handler_id FROM legal.contracts"):
        assert r["owner_id"] == r["handler_id"]


def test_screenings_get_the_contract_label_and_need_a_reason(synth_small):
    """hr.screenings starts empty; the trigger fills the §5.1 label when a writer forgets it, and a decision
    without a reason and a deciding person is rejected (GDPR Art. 22: meaningful human decision)."""
    import psycopg

    with pgmod.connect_owner() as c:
        c.execute("INSERT INTO hr.screenings (screening_id, candidate_id, req_id, created_by) "
                  "VALUES ('S-TEST', 'CAND-001', 'R-01', 'u0051')")
    try:
        row = _rows("SELECT level, acl_groups, subject_ids FROM hr.screenings WHERE screening_id = 'S-TEST'")[0]
        assert row == {"level": 4, "acl_groups": ["req:R-01", "head:hr"], "subject_ids": ["CAND-001"]}
        with pytest.raises(psycopg.errors.CheckViolation):
            with pgmod.connect_owner() as c:
                c.execute("UPDATE hr.screenings SET decision = 'reject' WHERE screening_id = 'S-TEST'")
        with pytest.raises(psycopg.errors.CheckViolation):
            with pgmod.connect_owner() as c:  # L4 is fixed for screenings
                c.execute("UPDATE hr.screenings SET level = 3 WHERE screening_id = 'S-TEST'")
    finally:
        with pgmod.connect_owner() as c:
            c.execute("DELETE FROM hr.screenings WHERE screening_id = 'S-TEST'")
