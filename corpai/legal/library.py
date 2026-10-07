"""Shared reads for the legal services: start check, clause library, one contract row (all RLS)."""

from __future__ import annotations

import re

from corpai import audit
from corpai.db import pg
from corpai.policy import may_start
from corpai.types import Principal

AGENT = "legal_review"
CONTRACT_ID = re.compile(r"^C-[0-9]{4}$")


async def require_start(principal: Principal, run_id: str) -> None:
    """Only users who may start the legal_review agent (legal dept, CEO) run reviews and drafts.
    A refusal is explicit and audited (policy is pure; callers audit its decisions)."""
    d = may_start(principal, AGENT)
    if d.allow:
        return
    await audit.arecord("policy_decision", actor=principal.user_id, act_chain=[f"agent:{AGENT}"], action="start",
                        resource=f"agent:{AGENT}", decision="deny", reason=d.rule_id, run_id=run_id)
    raise PermissionError("you may not use the legal review app; ask the legal team")


async def clause_library(principal: Principal, run_id: str) -> list[dict]:
    """Rows of legal.clause_library the principal may read (RLS: legal dept, head of legal, CEO)."""
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute(
            "SELECT clause_id, clause_type, position, text, level FROM legal.clause_library ORDER BY clause_id")
        return [dict(r) for r in await cur.fetchall()]


async def contract_row(principal: Principal, contract_id: str, run_id: str) -> dict | None:
    if not isinstance(contract_id, str) or not CONTRACT_ID.fullmatch(contract_id):
        raise ValueError("contract_id must look like C-0001")
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute(
            "SELECT contract_id, account_id, contract_type, status, level, trust, handler_id, processes_personal_data, "
            "has_dpa_clause, encode(sha256(convert_to(text, 'UTF8')), 'hex') AS text_sha256 "
            "FROM legal.contracts WHERE contract_id = %s", (contract_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


def library_choice(library: list[dict], clause_type: str | None, position: str) -> dict | None:
    if not clause_type:
        return None
    return next((r for r in library if r["clause_type"] == clause_type and r["position"] == position), None)
