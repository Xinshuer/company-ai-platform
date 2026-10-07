"""Foundation integration: the seeded company (A5) read through real principals (A3) and RLS.

For each of the 14 personas this counts what RLS shows per business table and checks it against
architecture §5.1 and design-review 五. It also checks the cross-stream contracts that no single
stream can test alone: synth's corpus sources vs classify.source_default, the gazetteer against the
real directory/CRM/HR tables, and the audit chain after an owner job.
"""

from __future__ import annotations

import pytest

from corpai.synth.personas import PERSONAS, SERVICE_ACCOUNTS
from corpai.synth.writer import BUSINESS_TABLES
from corpai.types import Level, Role
from tests.integration.rows import TABLE_KIND, can_read, owner_rows, visible_rows

CRM = ("crm.accounts", "crm.contacts", "crm.opportunities", "crm.interactions", "crm.quotes")
FIN = ("fin.chart_of_accounts", "fin.journal_entries", "fin.invoices", "fin.budgets")
HR_RESTRICTED = ("hr.compensation", "hr.performance", "hr.screenings")
HEADS = {"sales_head": "sales", "eng_head": "eng", "legal_head": "legal", "finance_head": "finance", "hr_head": "hr"}

# §5.1 clearance: CEO and heads L4; legal/finance/hr staff L4; sales/eng/exec staff L3; IT roles L2.
CLEARANCE = {
    "ceo": 4, "exec_staff": 3, "sales_head": 4, "sales_staff": 3, "eng_head": 4, "eng_staff": 3,
    "legal_head": 4, "legal_staff": 4, "finance_head": 4, "finance_staff": 4, "hr_head": 4, "hr_staff": 4,
    "platform_admin": 2, "auditor": 2,
}


@pytest.fixture(scope="module")
def seen(personas) -> dict[str, dict[str, list[dict]]]:
    """persona key -> table -> rows visible through RLS."""
    return {key: visible_rows(p, BUSINESS_TABLES) for key, p in personas.items()}


@pytest.fixture(scope="module")
def total() -> dict[str, int]:
    return {t: owner_rows(f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in BUSINESS_TABLES}


def _n(seen, key: str, table: str) -> int:
    return len(seen[key][table])


def _ids(rows, col: str) -> set:
    return {r[col] for r in rows}


# ---- directory and principals ----------------------------------------------------------------

def test_every_business_table_has_a_policy_kind():
    assert set(BUSINESS_TABLES) == set(TABLE_KIND)


@pytest.mark.parametrize("key", sorted(PERSONAS))
def test_persona_principal_matches_the_directory_contract(personas, key):
    p = personas[key]
    assert p.user_id == PERSONAS[key] and p.active
    assert int(p.clearance) == CLEARANCE[key]
    assert {"all", f"dept:{p.dept.value}", f"user:{p.user_id}"} <= p.groups
    heads = {g for g in p.groups if g.startswith("head:")}
    if key in HEADS:
        assert p.role is Role.MANAGER and heads == {f"head:{HEADS[key]}"}
    elif key == "ceo":
        assert p.role is Role.CEO and heads == {"head:exec"} and "role:ceo" in p.groups
    else:
        assert heads == set()
    roles = {g for g in p.groups if g.startswith("role:")}
    assert roles == {"ceo": {"role:ceo"}, "platform_admin": {"role:platform_admin"},
                     "auditor": {"role:security_auditor"}}.get(key, set())


def test_relationship_memberships_match_the_synth_promises(personas):
    """§5.1: sales_staff owns >= 3 accounts, eng_staff is in >= 1 project, legal_staff handles >= 3
    contracts and 1 matter, hr_staff is on >= 1 requisition."""
    assert any(g.startswith("proj:") for g in personas["eng_staff"].groups)
    assert {g for g in personas["legal_staff"].groups if g.startswith("matter:")} == {"matter:M-01"}
    assert any(g.startswith("req:") for g in personas["hr_staff"].groups)
    owned = owner_rows("SELECT count(*) AS n FROM crm.accounts WHERE owner_id = %s", (PERSONAS["sales_staff"],))
    handled = owner_rows("SELECT count(*) AS n FROM legal.contracts WHERE handler_id = %s", (PERSONAS["legal_staff"],))
    assert owned[0]["n"] >= 3 and handled[0]["n"] >= 3


# ---- RLS = the label formula, for everyone ---------------------------------------------------

@pytest.mark.parametrize("key", sorted(PERSONAS))
def test_rls_shows_exactly_the_rows_the_label_formula_allows(personas, seen, key):
    """RLS neither hides a readable row nor shows an unreadable one (core.can_read on every table)."""
    p = personas[key]
    all_rows = {t: owner_rows(f"SELECT level, acl_groups FROM {t}") for t in BUSINESS_TABLES}
    for t in BUSINESS_TABLES:
        assert all(can_read(p, r) for r in seen[key][t]), t
        assert _n(seen, key, t) == sum(can_read(p, r) for r in all_rows[t]), t
        assert all(int(r["level"]) <= int(p.clearance) for r in seen[key][t]), t


@pytest.mark.parametrize("svc", SERVICE_ACCOUNTS)
def test_service_accounts_see_no_business_rows(seeded, svc):
    from corpai.identity import directory

    p = directory.get_principal_sync(svc)
    assert p is not None and p.role is Role.SERVICE and p.groups == frozenset({svc})
    assert all(rows == [] for rows in visible_rows(p, BUSINESS_TABLES).values())


# ---- design-review 五, persona by persona -----------------------------------------------------

def test_sales_staff_sees_only_own_accounts(seen, total):
    me = PERSONAS["sales_staff"]
    own = _ids(owner_rows("SELECT account_id FROM crm.accounts WHERE owner_id = %s", (me,)), "account_id")
    assert len(own) >= 3 and _ids(seen["sales_staff"]["crm.accounts"], "account_id") == own
    assert _n(seen, "sales_staff", "crm.accounts") < total["crm.accounts"]
    for t in CRM[1:]:
        rows = seen["sales_staff"][t]
        assert rows and _ids(rows, "account_id") <= own, t
        expect = owner_rows(f"SELECT count(*) AS n FROM {t} WHERE account_id = ANY(%s)", (list(own),))[0]["n"]
        assert len(rows) == expect, t
    # their own contract requests, project stage status (dept:sales), nothing from finance, HR or litigation
    assert _ids(seen["sales_staff"]["legal.contract_requests"], "requested_by") <= {me}
    assert _n(seen, "sales_staff", "eng.projects") == total["eng.projects"]
    for t in ("eng.tickets", "eng.vulnerabilities", "legal.contracts", "legal.matters", *FIN, "hr.employees",
              *HR_RESTRICTED, "hr.candidates"):
        assert _n(seen, "sales_staff", t) == 0, t


def test_sales_head_sees_every_account_but_no_finance_or_legal_files(seen, total):
    for t in CRM:
        assert _n(seen, "sales_head", t) == total[t], t
    assert _n(seen, "sales_head", "legal.contract_requests") == total["legal.contract_requests"]
    for t in ("legal.contracts", "legal.matters", *FIN, *HR_RESTRICTED, "eng.tickets", "eng.vulnerabilities"):
        assert _n(seen, "sales_head", t) == 0, t


def test_eng_staff_sees_no_crm_and_only_own_project_tickets(personas, seen, total):
    projects = {g.split(":", 1)[1] for g in personas["eng_staff"].groups if g.startswith("proj:")}
    for t in CRM:
        assert _n(seen, "eng_staff", t) == 0, t
    for t in ("eng.tickets", "eng.ci_runs"):
        rows = seen["eng_staff"][t]
        assert rows and _ids(rows, "project_id") <= projects, t
        expect = owner_rows(f"SELECT count(*) AS n FROM {t} WHERE project_id = ANY(%s)", (list(projects),))[0]["n"]
        assert len(rows) == expect < total[t], t
    assert _n(seen, "eng_staff", "eng.projects") == total["eng.projects"]  # stage status: all of eng
    for t in ("eng.vulnerabilities", "legal.contracts", *FIN, "hr.employees", *HR_RESTRICTED):
        assert _n(seen, "eng_staff", t) == 0, t


def test_eng_head_sees_all_engineering_including_vulnerabilities(seen, total):
    for t in ("eng.projects", "eng.tickets", "eng.ci_runs", "eng.vulnerabilities"):
        assert _n(seen, "eng_head", t) == total[t], t
    for t in (*CRM, "legal.contracts", *FIN, *HR_RESTRICTED):
        assert _n(seen, "eng_head", t) == 0, t


def test_legal_staff_sees_own_contracts_its_matter_and_the_granted_account(personas, seen, total):
    me = PERSONAS["legal_staff"]
    own = _ids(owner_rows("SELECT contract_id FROM legal.contracts WHERE handler_id = %s", (me,)), "contract_id")
    assert len(own) >= 3 and _ids(seen["legal_staff"]["legal.contracts"], "contract_id") == own
    assert _ids(seen["legal_staff"]["legal.matters"], "matter_id") == {"M-01"}
    assert _n(seen, "legal_staff", "legal.contract_requests") == total["legal.contract_requests"]  # dept:legal

    # design-review 五, CRM row: 法务凭工单临时查看单个客户. The synth grant must be live (core.principal_row()
    # compares expires_at with the real clock), and it opens exactly that one account, no more.
    grants = owner_rows("SELECT grp, expires_at > now() AS live FROM core.grants WHERE user_id = %s", (me,))
    assert [(g["grp"], g["live"]) for g in grants] == [("grant:crm:A-0003", True)]
    assert {g for g in personas["legal_staff"].groups if g.startswith("grant:")} == {"grant:crm:A-0003"}
    assert _ids(seen["legal_staff"]["crm.accounts"], "account_id") == {"A-0003"}
    for t in CRM[1:]:
        assert _ids(seen["legal_staff"][t], "account_id") <= {"A-0003"}, t
    assert seen["legal_staff"]["crm.contacts"], "the granted account's contacts are visible"
    for t in (*FIN, "hr.employees", *HR_RESTRICTED, "eng.tickets", "eng.vulnerabilities"):
        assert _n(seen, "legal_staff", t) == 0, t


def test_matters_are_legal_only(seen, total):
    assert _n(seen, "legal_head", "legal.matters") == total["legal.matters"] == 2
    readers = {k for k in PERSONAS if seen[k]["legal.matters"]}
    assert readers == {"legal_head", "legal_staff"}  # not even the CEO without a grant


def test_finance_staff_sees_the_ledger(seen, total):
    for t in FIN:
        assert total[t] > 0 and _n(seen, "finance_staff", t) == total[t], t
    for t in (*CRM, "legal.contracts", "hr.employees", *HR_RESTRICTED, "eng.tickets"):
        assert _n(seen, "finance_staff", t) == 0, t
    assert _n(seen, "finance_head", "hr.requisition_budgets") == total["hr.requisition_budgets"]


def test_hr_staff_sees_employees_but_no_pay_or_performance(personas, seen, total):
    assert _n(seen, "hr_staff", "hr.employees") == total["hr.employees"]
    assert _n(seen, "hr_staff", "hr.requisitions") == total["hr.requisitions"]
    for t in HR_RESTRICTED + ("hr.requisition_budgets",):
        assert _n(seen, "hr_staff", t) == 0, t
    reqs = {g.split(":", 1)[1] for g in personas["hr_staff"].groups if g.startswith("req:")}
    cands = seen["hr_staff"]["hr.candidates"]
    assert cands and _ids(cands, "req_id") <= reqs
    for t in (*CRM, *FIN, "legal.contracts", "eng.tickets"):
        assert _n(seen, "hr_staff", t) == 0, t


def test_pay_and_performance_are_hr_head_only(seen, total):
    for t in ("hr.compensation", "hr.performance"):
        assert total[t] > 0
        assert {k for k in PERSONAS if seen[k][t]} == {"hr_head"}, t
        assert _n(seen, "hr_head", t) == total[t]


def test_ceo_sees_business_but_no_matters_or_pay(seen, total):
    for t in (*CRM, *FIN, "legal.contracts", "eng.projects", "eng.vulnerabilities", "hr.requisitions",
              "hr.requisition_budgets"):
        assert _n(seen, "ceo", t) == total[t], t
    for t in ("legal.matters", "hr.compensation", "hr.performance", "hr.screenings", "hr.candidates"):
        assert _n(seen, "ceo", t) == 0, t
    # hr.employees: only the direct reports (mgr-of), never the whole company
    reports = {r["user_id"] for r in owner_rows("SELECT user_id FROM core.users WHERE manager_id = %s AND active",
                                                (PERSONAS["ceo"],))}
    assert _ids(seen["ceo"]["hr.employees"], "employee_id") == reports


@pytest.mark.parametrize("key", ["platform_admin", "auditor"])
def test_ops_roles_see_no_confidential_rows(seen, key):
    for t in BUSINESS_TABLES:
        assert all(int(r["level"]) <= 2 and "all" in r["acl_groups"] for r in seen[key][t]), t
    assert sum(len(v) for v in seen[key].values()) == len(seen[key]["legal.regulations"])


def test_exec_staff_sees_only_company_wide_material(seen):
    assert seen["exec_staff"]["legal.regulations"]
    for t in BUSINESS_TABLES:
        assert all(int(r["level"]) <= 2 and "all" in r["acl_groups"] for r in seen["exec_staff"][t]), t


def test_public_library_is_readable_by_every_persona(seen, total):
    for key in PERSONAS:
        assert _n(seen, key, "legal.regulations") == total["legal.regulations"] > 0, key


# ---- cross-stream contracts --------------------------------------------------------------------

@pytest.mark.parametrize("small", [True, False])
def test_corpus_sources_default_to_their_front_matter_level(small):
    """Ingestion labels a document max(source_default(source), classify_text(text)). A source default above
    the synth label would over-label it; one the inventory does not know is L4 (fail closed). Both are bugs."""
    from corpai import synth
    from corpai.classify import source_default

    from corpai.classify import classify_text

    world = synth.generate(7, small=small)
    # §5.1: a doc hosting a canary is L4 whatever its source; classify_text raises it there at ingestion.
    canary_docs = {d.doc_id for d in world.docs if "KB-CANARY-" in d.body}
    assert canary_docs and all(d.level == 4 for d in world.docs if d.doc_id in canary_docs)
    assert all(classify_text(d.body).level == 4 for d in world.docs if d.doc_id in canary_docs)
    wrong = sorted({(d.source, d.level, int(source_default(d.source))) for d in world.docs
                    if int(source_default(d.source)) != d.level and d.doc_id not in canary_docs})
    assert wrong == []


def test_db_tables_default_to_their_row_level(seeded):
    """source_default(<table>) is the label a row of that table gets unless its content says more: it must equal
    the lowest level synth writes there (canary rows may sit above it)."""
    from corpai.classify import source_default

    wrong = []
    for t in BUSINESS_TABLES:
        lo = owner_rows(f"SELECT min(level) AS lo FROM {t}")[0]["lo"]
        expect = Level.L4 if lo is None else Level(lo)  # hr.screenings is empty in the seed; it is L4 by CHECK
        if source_default(t) != expect:
            wrong.append((t, int(source_default(t)), int(expect)))
    assert wrong == []


@pytest.fixture
def restore_gazetteer(monkeypatch):
    # The gazetteer is process-global; later tests (trace masking) must see the state they expect.
    from corpai.classify import names as names_mod

    monkeypatch.setattr(names_mod, "_state", names_mod._state)


def test_gazetteer_loads_names_from_the_seeded_tables(seeded, restore_gazetteer):
    from corpai.classify import classify_text, load_gazetteer_from_db

    humans = owner_rows("SELECT name FROM core.users WHERE role <> 'service'")
    contact = owner_rows("SELECT name FROM crm.contacts ORDER BY contact_id LIMIT 1")[0]["name"]
    candidate = owner_rows("SELECT name FROM hr.candidates ORDER BY candidate_id LIMIT 1")[0]["name"]
    assert load_gazetteer_from_db() >= len(humans)
    for name in ("Erik Lindqvist", contact, candidate):
        found = [f.kind for f in classify_text(f"Please call {name} tomorrow about the renewal.").findings]
        assert "person_name" in found, name
    # Swedish staff names with å/ä/ö match as whole names, wrapped across a line too, and make the text L3
    for name in ("Daniel Sjögren", "Maria Engström", "Johan Ekström"):
        for text in (f"Ask {name} about the rollout.", f"Ask {name.replace(' ', chr(10))} about the rollout."):
            r = classify_text(text)
            names = [text[f.start:f.end] for f in r.findings if f.kind == "person_name"]
            assert names == [text[4:4 + len(name)]] and r.level == Level.L3, (text, names, r.level)
    # e-mail local parts fold å/ä/ö to a/a/o
    assert owner_rows("SELECT email FROM core.users WHERE user_id = 'u0060'")[0]["email"] == \
        "daniel.sjogren@abctech.example"


def test_owner_job_audit_and_chain_after_seeding(seeded):
    from corpai import audit

    rows = owner_rows("SELECT actor, details FROM audit.log WHERE event = 'owner_job' AND actor = 'svc:synth'")
    assert rows, "synth.run must audit its owner job"
    report = audit.verify_chain()
    assert report.ok and report.checked >= len(rows), report
