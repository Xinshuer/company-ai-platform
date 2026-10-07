"""Shared helpers for the integration tests: read seeded business rows as the owner or through RLS,
and turn a row's label columns into a policy Resource (architecture §5.1: the data contract between
synth, policy and RLS)."""

from __future__ import annotations

from typing import Any, Mapping

from corpai.db import pg
from corpai.types import Dept, Label, Level, Principal, Resource, Trust

# Business table -> policy resource kind (corpai.policy.LABELLED_KINDS).
TABLE_KIND: dict[str, str] = {
    "crm.accounts": "crm.account",
    "crm.contacts": "crm.contact",
    "crm.opportunities": "crm.opportunity",
    "crm.interactions": "crm.interaction",
    "crm.quotes": "crm.quote",
    "eng.projects": "eng.project",
    "eng.tickets": "eng.ticket",
    "eng.ci_runs": "eng.ci_run",
    "eng.vulnerabilities": "eng.vuln",
    "legal.contracts": "legal.contract",
    "legal.contract_requests": "legal.contract_request",
    "legal.matters": "legal.matter",
    "legal.regulations": "legal.regulation",
    "legal.clause_library": "legal.clause",
    "fin.chart_of_accounts": "fin.ledger",  # reference data of the ledger; same finance ACL
    "fin.journal_entries": "fin.ledger",
    "fin.invoices": "fin.invoice",
    "fin.budgets": "fin.budget",
    "hr.employees": "hr.employee",
    "hr.compensation": "hr.compensation",
    "hr.performance": "hr.performance",
    "hr.requisitions": "hr.requisition",
    "hr.requisition_budgets": "hr.requisition_budget",
    "hr.candidates": "hr.candidate",
    "hr.screenings": "hr.screening",
}

_DEPTS = {d.value for d in Dept}


def pk_columns(table: str) -> list[str]:
    schema, name = table.split(".")
    with pg.connect_owner() as c:
        rows = c.execute(
            """SELECT a.attname AS col FROM pg_index i
               JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
               WHERE i.indrelid = (quote_ident(%s) || '.' || quote_ident(%s))::regclass AND i.indisprimary
               ORDER BY array_position(i.indkey::int2[], a.attnum)""", (schema, name)).fetchall()
    return [r["col"] for r in rows]


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    with pg.connect_owner() as c:
        return c.execute(sql, params).fetchall()


def visible_rows(p: Principal, tables) -> dict[str, list[dict]]:
    """Every row each table shows `p` through RLS, in one principal transaction."""
    with pg.principal_tx_sync(p) as c:
        return {t: c.execute(f"SELECT * FROM {t}").fetchall() for t in tables}  # table names are constants


def label_of(row: Mapping[str, Any]) -> Label:
    dept = row.get("dept")
    return Label(level=Level(int(row["level"])), dept=Dept(dept) if dept in _DEPTS else None,
                 owner_id=row.get("owner_id"), acl_groups=frozenset(row["acl_groups"]),
                 trust=Trust(row.get("trust") or "internal"), subject_ids=frozenset(row.get("subject_ids") or ()))


def resource_of(table: str, row: Mapping[str, Any], key: tuple) -> Resource:
    return Resource(kind=TABLE_KIND[table], id=":".join(str(k) for k in key), label=label_of(row))


def can_read(p: Principal, row: Mapping[str, Any]) -> bool:
    """core.can_read in Python: level <= clearance AND acl && groups."""
    return int(row["level"]) <= int(p.clearance) and bool(set(row["acl_groups"]) & p.groups)
