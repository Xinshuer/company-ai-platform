"""Write proposals: AI proposes -> a person approves -> the executor runs it -> audit (architecture §11).

Public API (async; used by the MCP write tools and the API):
    create(principal, *, agent, run_id, domain, tool, params, summary, ...) -> Proposal   (status pending)
    approve(approver, proposal_id, *, params_hash, reason) -> Proposal   (then executes: executed | failed)
    reject(approver, proposal_id, *, reason) -> Proposal
    execute(proposal_id) -> Proposal          executor job (owner role); execute_sync for scripts
    mark_failed(proposal_id, error), fail_interrupted(older_than_s)   owner jobs: no proposal stays 'approved'
    get(principal, proposal_id) -> Proposal | None;  list_for(principal, box='mine'|'to_approve') -> list[dict]
    describe(principal, proposal) -> dict     §18 API shape with can_approve / why_not
    params_hash(params), canonical_params(params), EXECUTORS, errors below
"""

from __future__ import annotations

from corpai.proposals.executors import EXECUTORS, ExecutorError
from corpai.proposals.model import (
    ParamsHashMismatch,
    Proposal,
    ProposalConflict,
    ProposalError,
    ProposalExpired,
    ProposalForbidden,
    ProposalInvalid,
    ProposalNotFound,
    canonical_params,
    params_hash,
)
from corpai.proposals.service import (
    approval_check,
    approve,
    create,
    describe,
    execute,
    execute_sync,
    fail_interrupted,
    get,
    list_for,
    mark_failed,
    reject,
)

__all__ = [
    "EXECUTORS", "ExecutorError", "ParamsHashMismatch", "Proposal", "ProposalConflict", "ProposalError",
    "ProposalExpired", "ProposalForbidden", "ProposalInvalid", "ProposalNotFound", "approval_check", "approve",
    "canonical_params", "create", "describe", "execute", "execute_sync", "fail_interrupted", "get", "list_for",
    "mark_failed", "params_hash", "reject",
]
