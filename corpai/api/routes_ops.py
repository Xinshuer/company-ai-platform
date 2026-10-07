"""Audit log (security auditor) and outage switches (platform admin), phase-B spec §18.

The auditor's reads are themselves audited (event audit_read, written before the read so that a
read that cannot be recorded does not happen). The platform admin never reads the audit log and
the auditor never flips switches: the two roles check each other.
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from corpai import audit
from corpai.api import deps
from corpai.api.errors import bad_request, forbidden
from corpai.gateway import ops
from corpai.types import Principal, Role

router = APIRouter()

_ENTRY_KEYS = ("seq", "ts", "actor", "act_chain", "event", "action", "resource", "decision", "reason", "route",
               "level", "approval_id", "run_id", "details", "prev_hash", "hash")


def _entry(row: dict) -> dict:
    out = {k: row.get(k) for k in _ENTRY_KEYS}
    if hasattr(out["ts"], "isoformat"):
        out["ts"] = out["ts"].isoformat()
    out["act_chain"] = list(out["act_chain"] or [])
    return out


@router.get("/api/audit")
async def read_audit(after_seq: int = 0, limit: int = 200, p: Principal = Depends(deps.principal)) -> dict:
    if not (p.active and p.role is Role.SECURITY_AUDITOR):
        await audit.arecord("audit_read", actor=p.user_id, action="read_log", resource="audit.log", decision="deny",
                            reason="not_security_auditor")
        raise forbidden("Only the security auditor reads the audit log.")
    if after_seq < 0 or not 1 <= limit <= 1000:
        raise bad_request("after_seq must be >= 0 and limit between 1 and 1000.")
    await audit.arecord("audit_read", actor=p.user_id, action="read_log", resource="audit.log", decision="allow",
                        reason="auditor", details={"after_seq": after_seq, "limit": limit})
    try:
        rows = await audit.read_log(p, after_seq=after_seq, limit=limit)
    except PermissionError:
        raise forbidden("Only the security auditor reads the audit log.") from None
    report = await anyio.to_thread.run_sync(audit.verify_chain)  # owner-side check incl. external anchors
    return {"entries": [_entry(r) for r in rows],
            "chain": {"ok": bool(report.ok), "checked": int(report.checked), "first_bad_seq": report.first_bad_seq}}


def _require_admin(p: Principal) -> None:
    if not (p.active and p.role is Role.PLATFORM_ADMIN):
        raise forbidden("Only the platform admin manages outage switches.")


@router.get("/api/admin/switches")
async def get_switches(p: Principal = Depends(deps.principal)) -> dict:
    _require_admin(p)
    return await ops.get_switches()


class SwitchIn(BaseModel):
    name: str = Field(max_length=64)
    on: bool
    reason: str = Field(max_length=500)


@router.post("/api/admin/switches")
async def set_switch(body: SwitchIn, p: Principal = Depends(deps.principal)) -> Any:
    is_admin = p.active and p.role is Role.PLATFORM_ADMIN
    try:
        await ops.set_switch(p, body.name, body.on, body.reason)  # audits allow and deny itself
    except ValueError:
        if not is_admin:  # a non-admin learns nothing about which inputs would be valid
            raise forbidden("Only the platform admin manages outage switches.") from None
        raise bad_request("Give a known switch (local_down or external_down), on true/false and a reason.") from None
    except PermissionError:
        raise forbidden("Only the platform admin manages outage switches.") from None
    return await ops.get_switches()
