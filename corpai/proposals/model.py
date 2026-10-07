"""Proposal value type, canonical params hashing and the errors of the proposals package."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from corpai.types import Level, ToolTier

STATUSES = frozenset({"pending", "approved", "rejected", "executed", "failed", "expired"})


def canonical_params(params: Mapping[str, Any]) -> str:
    """The one JSON form that is hashed, stored (ops.proposals.params_text) and executed.

    sort_keys + compact separators + ensure_ascii=False; NaN/Infinity refused (not JSON)."""
    if not isinstance(params, Mapping):
        raise ProposalInvalid("params must be an object")
    try:
        return json.dumps(dict(params), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ProposalInvalid("params must be plain JSON (strings, numbers, booleans, lists, objects)") from None


def params_hash(params: Mapping[str, Any] | str) -> str:
    """sha256 hex of the canonical JSON (a str is taken as already canonical)."""
    text = params if isinstance(params, str) else canonical_params(params)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Proposal:
    id: str
    created_at: datetime
    requester_id: str
    agent: str
    run_id: str | None
    thread_id: str | None
    domain: str
    tool: str
    tier: ToolTier
    params: dict
    params_text: str
    params_hash: str
    summary: str
    status: str
    approver_id: str | None
    decided_at: datetime | None
    reason: str | None
    result: dict | None
    executed_at: datetime | None
    expires_at: datetime
    level: Level
    acl_groups: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Proposal":
        return cls(
            id=row["id"], created_at=row["created_at"], requester_id=row["requester_id"], agent=row["agent"],
            run_id=row.get("run_id"), thread_id=row.get("thread_id"), domain=row["domain"], tool=row["tool"],
            tier=ToolTier(int(row["tier"])), params=dict(row["params"]), params_text=row["params_text"],
            params_hash=row["params_hash"], summary=row["summary"], status=row["status"],
            approver_id=row.get("approver_id"), decided_at=row.get("decided_at"), reason=row.get("reason"),
            result=row.get("result"), executed_at=row.get("executed_at"), expires_at=row["expires_at"],
            level=Level(int(row["level"])), acl_groups=tuple(row.get("acl_groups") or ()),
        )

    def to_api(self, *, requester_name: str | None = None, can_approve: bool = False,
               why_not: str | None = None, approver_name: str | None = None) -> dict:
        """The §18 shape (GET /api/proposals): full real parameters and their hash for the approver."""
        return {
            "proposal_id": self.id,
            "created_at": self.created_at.isoformat(),
            "requester": {"user_id": self.requester_id, "name": requester_name},
            "agent": self.agent,
            "domain": self.domain,
            "tool": self.tool,
            "tier": int(self.tier),
            "status": self.status,
            "summary": self.summary,
            "params": self.params,
            "params_hash": self.params_hash,
            "level": int(self.level),
            "expires_at": self.expires_at.isoformat(),
            "approver_id": self.approver_id,
            "approver": {"user_id": self.approver_id, "name": approver_name} if self.approver_id else None,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "reason": self.reason,
            "result": self.result,
            "can_approve": can_approve,
            "why_not": why_not,
        }


class ProposalError(Exception):
    """Base class; messages are short, fixed and never contain parameter values."""

    code = "proposal_error"


class ProposalInvalid(ProposalError, ValueError):
    code = "bad_request"


class ProposalNotFound(ProposalError, LookupError):
    """Unknown id OR not readable by the caller: the two are deliberately indistinguishable."""

    code = "not_found"


class ProposalForbidden(ProposalError, PermissionError):
    code = "forbidden"


class ProposalConflict(ProposalError):
    """Not pending (already decided, executed, failed or expired)."""

    code = "conflict"


class ProposalExpired(ProposalConflict):
    code = "expired"


class ParamsHashMismatch(ProposalError):
    """The approver was shown different parameters than the ones stored (or they changed)."""

    code = "params_hash_mismatch"
