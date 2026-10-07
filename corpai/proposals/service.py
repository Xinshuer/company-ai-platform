"""create / approve / reject / execute / list_for (architecture §11, design-review 二.12).

Request-path functions (create, approve, reject, get, list_for) run as corpai_app inside
principal_tx and change ops.proposals only through the SECURITY DEFINER functions of migration 060,
which re-check identity, four-eyes and the params hash in SQL. execute() is the executor job: it
uses the owner connection, re-checks the hash, runs EXECUTORS[(domain, tool)], audits and writes
an owner_job event. Audit rows carry ids, tier, hash and status, never parameter values.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import anyio
import psycopg

from corpai import audit
from corpai.classify import classify_text
from corpai.db import pg
from corpai.ids import new_id
from corpai.policy import ProposalView, can_approve, tool_tier
from corpai.proposals.executors import EXECUTORS, ExecutorError
from corpai.proposals.model import (
    ParamsHashMismatch,
    Proposal,
    ProposalConflict,
    ProposalExpired,
    ProposalForbidden,
    ProposalInvalid,
    ProposalNotFound,
    canonical_params,
    params_hash,
)
from corpai.types import Level, Principal, Role, ToolTier

_CREATE_SQL = ("SELECT * FROM ops.proposal_create(%s, %s, %s, %s, %s, %s, %s::smallint, %s, %s, %s, %s::smallint,"
               " %s::text[])")
_DECIDE_SQL = "SELECT * FROM ops.proposal_decide(%s, %s, %s, %s)"
_GET_SQL = "SELECT * FROM ops.proposals WHERE id = %s"
_MAX_PARAMS_CHARS = 200_000

log = logging.getLogger(__name__)


def _act(agent: str | None) -> list[str]:
    return [f"agent:{agent}"] if agent else []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_reason(reason: Any) -> str:
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ProposalInvalid("a decision needs a reason (1-2000 characters)")
    return reason.strip()


async def _audit(event: str, actor: str, prop_id: str | None, *, agent: str | None = None, decision: str,
                 reason: str, action: str | None = None, level: int | None = None, run_id: str | None = None,
                 details: Mapping | None = None) -> None:
    await audit.arecord(event, actor=actor, act_chain=_act(agent), action=action,
                        resource=f"proposal:{prop_id}" if prop_id else None, decision=decision, reason=reason,
                        level=level, approval_id=prop_id, run_id=run_id, details=details or {})


# ---- create ---------------------------------------------------------------------------------

async def create(principal: Principal, *, agent: str, run_id: str | None, domain: str, tool: str,
                 params: Mapping[str, Any], summary: str, thread_id: str | None = None,
                 tier: int | ToolTier | None = None, level_floor: Level | int = Level.L1,
                 subject_ids: Iterable[str] = ()) -> Proposal:
    """File a pending proposal for `principal`. tier = max(claimed, catalogue tier); level =
    max(classify(params), classify(summary), level_floor) so the proposal is at least as
    sensitive as the row it targets."""
    if not principal.active or principal.role is Role.SERVICE:
        raise ProposalForbidden("only an active user may file a proposal")
    catalogue = tool_tier(domain, tool)
    if catalogue is None or catalogue is ToolTier.READ:
        raise ProposalInvalid(f"{domain}.{tool} is not a write tool")
    try:
        claimed = ToolTier(int(tier)) if tier is not None else catalogue
    except (TypeError, ValueError):
        raise ProposalInvalid("tier must be 2 or 3") from None
    eff_tier = max(claimed, catalogue)
    text = canonical_params(params)
    if len(text) > _MAX_PARAMS_CHARS:
        raise ProposalInvalid("params are too large")
    if not isinstance(summary, str) or not summary.strip():
        raise ProposalInvalid("a proposal needs a summary")
    summary = summary.strip()[:2000]
    level = max(int(classify_text(text).level), int(classify_text(summary).level), int(level_floor))
    prop_id = new_id("prop")
    digest = params_hash(text)
    try:
        async with pg.principal_tx(principal, run_id=run_id) as conn:
            cur = await conn.execute(_CREATE_SQL, (prop_id, agent, run_id or "", thread_id or "", domain, tool,
                                                   int(eff_tier), text, digest, summary, level, list(subject_ids)))
            row = await cur.fetchone()
    except psycopg.errors.InsufficientPrivilege:
        await _audit("proposal_created", principal.user_id, None, agent=agent, decision="deny",
                     reason="refused_by_database", action=f"{domain}.{tool}", run_id=run_id)
        raise ProposalForbidden("only an active user may file a proposal") from None
    prop = Proposal.from_row(row)
    await _audit("proposal_created", principal.user_id, prop.id, agent=agent, decision="allow", reason="pending",
                 action=f"{domain}.{tool}", level=int(prop.level), run_id=run_id,
                 details={"domain": domain, "tool": tool, "tier": int(prop.tier), "params_hash": prop.params_hash})
    return prop


# ---- read -----------------------------------------------------------------------------------

async def get(principal: Principal, proposal_id: str) -> Proposal | None:
    """The proposal if the caller may read it (RLS: requester and possible approvers), else None."""
    if not isinstance(proposal_id, str) or not proposal_id.startswith("prop_") or len(proposal_id) > 64:
        return None
    async with pg.principal_tx(principal) as conn:
        cur = await conn.execute(_GET_SQL, (proposal_id,))
        row = await cur.fetchone()
    return Proposal.from_row(row) if row else None


def _view(prop: Proposal) -> ProposalView:
    return ProposalView(requester_id=prop.requester_id, domain=prop.domain, tool=prop.tool, tier=prop.tier)


def approval_check(principal: Principal, prop: Proposal, *,
                   requester_active: bool = True) -> tuple[bool, str | None]:
    """(can_approve now, why not): policy plus the state the proposal is in."""
    dec = can_approve(principal, _view(prop))
    if not dec.allow:
        return False, dec.reason
    if prop.status != "pending":
        return False, f"the proposal is {prop.status}"
    if prop.expires_at <= _now():
        return False, "the proposal has expired"
    if not requester_active:
        return False, "the requester is no longer active"
    return True, None


async def _people(ids: Iterable[str]) -> dict[str, Principal | None]:
    """Re-read from the directory on every call (no cache): deactivation counts at once."""
    from corpai.identity import directory

    return {uid: await directory.get_principal(uid) for uid in set(ids)}


def _name(people: Mapping[str, Principal | None], uid: str | None) -> str | None:
    p = people.get(uid) if uid else None
    return p.name if p else None


def _active(people: Mapping[str, Principal | None], uid: str) -> bool:
    p = people.get(uid)
    return p is not None and p.active


async def describe(principal: Principal, prop: Proposal) -> dict:
    """§18 API shape of one proposal for this viewer."""
    people = await _people([prop.requester_id, *([prop.approver_id] if prop.approver_id else [])])
    ok, why = approval_check(principal, prop, requester_active=_active(people, prop.requester_id))
    return prop.to_api(requester_name=_name(people, prop.requester_id), can_approve=ok, why_not=why,
                       approver_name=_name(people, prop.approver_id))


async def list_for(principal: Principal, box: str = "mine", *, limit: int = 200) -> list[dict]:
    """box='mine': the caller's own proposals; box='to_approve': pending ones the caller may approve."""
    if box not in ("mine", "to_approve"):
        raise ProposalInvalid("box must be 'mine' or 'to_approve'")
    limit = max(1, min(int(limit), 500))
    async with pg.principal_tx(principal) as conn:
        if box == "mine":
            cur = await conn.execute("SELECT * FROM ops.proposals WHERE requester_id = %s"
                                     " ORDER BY created_at DESC LIMIT %s", (principal.user_id, limit))
        else:
            cur = await conn.execute("SELECT * FROM ops.proposals WHERE status = 'pending' AND expires_at > now()"
                                     " ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = await cur.fetchall()
    props = [Proposal.from_row(r) for r in rows]
    people = await _people([p.requester_id for p in props] + [p.approver_id for p in props if p.approver_id])
    out = []
    for prop in props:
        # a deactivated requester's pending proposals drop out of every inbox: nothing may run in
        # their name any more (approve() refuses and lets them lapse)
        ok, why = approval_check(principal, prop, requester_active=_active(people, prop.requester_id))
        if box == "to_approve" and not ok:
            continue
        out.append(prop.to_api(requester_name=_name(people, prop.requester_id), can_approve=ok, why_not=why,
                               approver_name=_name(people, prop.approver_id)))
    return out


# ---- decide ---------------------------------------------------------------------------------

def _sql_error(e: psycopg.Error) -> Exception:
    state = getattr(e, "sqlstate", None)
    if state == "P0002":
        return ProposalNotFound("proposal not found")
    if state == "55000":
        return ProposalConflict("the proposal is not pending")
    if state == "42501":
        return ProposalForbidden("you may not decide on this proposal")
    if state == "22023":
        return ParamsHashMismatch("the parameters changed since you looked; reload and review again")
    return ProposalConflict("the decision could not be recorded")


async def _decide(principal: Principal, prop_id: str, decision: str, digest: str | None, reason: str) -> Proposal:
    try:
        async with pg.principal_tx(principal) as conn:
            cur = await conn.execute(_DECIDE_SQL, (prop_id, decision, digest, reason))
            row = await cur.fetchone()
    except psycopg.Error as e:
        raise _sql_error(e) from None
    return Proposal.from_row(row)


async def approve(approver: Principal, proposal_id: str, *, params_hash: str, reason: str) -> Proposal:
    """Approve with the exact params_hash the approver was shown, then execute. Returns the
    proposal after execution (status executed or failed). Every refusal is audited."""
    reason = _check_reason(reason)

    async def deny(prop: Proposal | None, why: str, exc: Exception) -> None:
        await _audit("proposal_approved", approver.user_id, prop.id if prop else proposal_id,
                     agent=prop.agent if prop else None, decision="deny", reason=why,
                     action=f"{prop.domain}.{prop.tool}" if prop else None,
                     level=int(prop.level) if prop else None, run_id=prop.run_id if prop else None,
                     details={"tier": int(prop.tier)} if prop else {})
        raise exc

    prop = await get(approver, proposal_id)
    if prop is None:
        await deny(None, "not_found", ProposalNotFound("proposal not found"))
    dec = can_approve(approver, _view(prop))
    if not dec.allow:
        await deny(prop, dec.rule_id, ProposalForbidden(dec.reason))
    if prop.status != "pending":
        await deny(prop, f"status_{prop.status}", ProposalConflict(f"the proposal is {prop.status}"))
    if prop.expires_at <= _now():
        await _decide(approver, prop.id, "approved", params_hash, reason)  # SQL marks it expired
        await deny(prop, "expired", ProposalExpired("the proposal has expired; ask for a new one"))
    if not isinstance(params_hash, str) or params_hash != prop.params_hash:
        await deny(prop, "params_hash_mismatch",
                   ParamsHashMismatch("the parameters changed since you looked; reload and review again"))
    if not _active(await _people([prop.requester_id]), prop.requester_id):
        # Nothing runs in a deactivated user's name (deactivation takes effect at once, §5). The SQL
        # function sees the same and lets the proposal lapse (status expired).
        try:
            await _decide(approver, prop.id, "approved", params_hash, reason)
        except ProposalConflict:
            pass
        await deny(prop, "requester_inactive",
                   ProposalExpired("the requester is no longer active; the proposal lapsed"))
    try:
        decided = await _decide(approver, prop.id, "approved", params_hash, reason)
    except Exception as e:  # the SQL re-check refused: audit it the same way
        await deny(prop, getattr(e, "code", "refused"), e)
    if decided.status == "expired":
        why = "requester_inactive" if decided.reason == "requester no longer active" else "expired"
        await deny(prop, why, ProposalExpired("the proposal has lapsed; ask for a new one"))
    await _audit("proposal_approved", approver.user_id, prop.id, agent=prop.agent, decision="allow", reason=dec.rule_id,
                 action=f"{prop.domain}.{prop.tool}", level=int(prop.level), run_id=prop.run_id,
                 details={"tier": int(prop.tier), "params_hash": prop.params_hash,
                          "requester_id": prop.requester_id})
    try:
        return await execute(prop.id)
    except Exception as e:  # noqa: BLE001 - never leave an approved proposal that nothing will run
        # The decision is committed. Whatever broke (owner connection, the audit write that rolls
        # the executor back on purpose), the proposal must not stay 'approved' forever with its
        # thread blocked: record it as failed, so the requester's thread resumes as not sent.
        log.warning("executing %s failed: %s", prop.id, type(e).__name__)
        return await anyio.to_thread.run_sync(mark_failed, prop.id, "executor_error", type(e).__name__)


async def reject(approver: Principal, proposal_id: str, *, reason: str) -> Proposal:
    """Reject (an eligible approver) or withdraw (the requester) a pending proposal."""
    reason = _check_reason(reason)
    prop = await get(approver, proposal_id)

    async def deny(why: str, exc: Exception) -> None:
        await _audit("proposal_rejected", approver.user_id, prop.id if prop else proposal_id,
                     agent=prop.agent if prop else None, decision="deny", reason=why,
                     action=f"{prop.domain}.{prop.tool}" if prop else None, run_id=prop.run_id if prop else None)
        raise exc

    if prop is None:
        await deny("not_found", ProposalNotFound("proposal not found"))
    is_requester = prop.requester_id == approver.user_id and approver.active
    dec = can_approve(approver, _view(prop))
    if not (is_requester or dec.allow):
        await deny(dec.rule_id, ProposalForbidden(dec.reason))
    if prop.status != "pending":
        await deny(f"status_{prop.status}", ProposalConflict(f"the proposal is {prop.status}"))
    try:
        decided = await _decide(approver, prop.id, "rejected", None, reason)
    except Exception as e:
        await deny(getattr(e, "code", "refused"), e)
    if decided.status == "expired":
        await deny("expired", ProposalExpired("the proposal has expired"))
    await _audit("proposal_rejected", approver.user_id, prop.id, agent=prop.agent, decision="allow",
                 reason="withdrawn_by_requester" if is_requester else dec.rule_id,
                 action=f"{prop.domain}.{prop.tool}", level=int(prop.level), run_id=prop.run_id,
                 details={"tier": int(prop.tier)})
    return decided


# ---- execute (executor job, owner role) ------------------------------------------------------

def _params_intact(prop: Proposal) -> dict | None:
    """The params to execute, or None when the stored text, its hash and the jsonb disagree."""
    try:
        parsed = json.loads(prop.params_text)
    except ValueError:
        return None
    if params_hash(prop.params_text) != prop.params_hash or canonical_params(parsed) != prop.params_text:
        return None
    if parsed != prop.params:  # someone edited the jsonb column behind the text
        return None
    return parsed


def execute_sync(proposal_id: str) -> Proposal:
    """Run an approved proposal. Status becomes executed or failed; never raises for a failed
    business precondition (that is recorded on the proposal). Raises ProposalNotFound /
    ProposalConflict when there is nothing approved to run."""
    with pg.connect_owner() as conn:
        row = conn.execute("SELECT * FROM ops.proposals WHERE id = %s FOR UPDATE", (proposal_id,)).fetchone()
        if row is None:
            raise ProposalNotFound("proposal not found")
        prop = Proposal.from_row(row)
        if prop.status != "approved":
            raise ProposalConflict(f"the proposal is {prop.status}, not approved")
        params = _params_intact(prop)
        fn = EXECUTORS.get((prop.domain, prop.tool))
        requester = conn.execute("SELECT active FROM core.users WHERE user_id = %s", (prop.requester_id,)).fetchone()
        if requester is None or not requester["active"]:
            # deactivated between approval and execution: nothing runs in their name
            status, result = "failed", {"error": "requester_inactive"}
        elif params is None:
            status, result = "failed", {"error": "params_hash_mismatch"}
        elif fn is None:
            status, result = "failed", {"error": "no_executor"}
        else:
            try:
                with conn.transaction():  # savepoint: a failing executor leaves no partial writes
                    result = fn(conn, prop, params)
                status = "executed"
            except ExecutorError as e:
                status, result = "failed", {"error": e.code, "message": str(e)}
            except Exception as e:  # noqa: BLE001 - any failure is recorded on the proposal, nothing half-written
                status, result = "failed", {"error": "executor_error", "type": type(e).__name__}
        updated = conn.execute(
            "UPDATE ops.proposals SET status = %s, result = %s::jsonb, executed_at = now() WHERE id = %s RETURNING *",
            (status, json.dumps(result, default=str), prop.id)).fetchone()
        # Audited inside the transaction: if the audit write fails, the execution rolls back.
        audit.record("proposal_executed", actor="svc:executor", act_chain=_act(prop.agent),
                     action=f"{prop.domain}.{prop.tool}", resource=f"proposal:{prop.id}",
                     decision="allow" if status == "executed" else "error",
                     reason=status if status == "executed" else str(result.get("error")),
                     level=int(prop.level), approval_id=prop.id, run_id=prop.run_id,
                     details={"tier": int(prop.tier), "requester_id": prop.requester_id,
                              "approver_id": prop.approver_id,
                              "result_ids": {k: v for k, v in result.items() if k.endswith("_id")}})
    try:
        audit.record_owner_job("executor", {"proposal_id": prop.id, "status": status})
    except Exception as e:  # noqa: BLE001 - committed and audited above; a missed anchor is not a failed run
        log.warning("owner_job record for %s failed: %s", prop.id, type(e).__name__)
    return Proposal.from_row(updated)


async def execute(proposal_id: str) -> Proposal:
    return await anyio.to_thread.run_sync(execute_sync, proposal_id)


def mark_failed(proposal_id: str, error: str, error_type: str | None = None) -> Proposal:
    """Owner job: an approved proposal whose execution broke off becomes 'failed' (nothing of the
    executor's work was committed: its writes, status and audit row share one transaction).
    Returns the proposal as stored; one that was executed after all is left as it is."""
    result = {"error": error, **({"type": error_type} if error_type else {})}
    with pg.connect_owner() as conn:
        row = conn.execute("UPDATE ops.proposals SET status = 'failed', result = %s::jsonb, executed_at = now()"
                           " WHERE id = %s AND status = 'approved' RETURNING *",
                           (json.dumps(result), proposal_id)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM ops.proposals WHERE id = %s", (proposal_id,)).fetchone()
            if row is None:
                raise ProposalNotFound("proposal not found")
            return Proposal.from_row(row)
    prop = Proposal.from_row(row)
    try:
        audit.record("proposal_executed", actor="svc:executor", act_chain=_act(prop.agent),
                     action=f"{prop.domain}.{prop.tool}", resource=f"proposal:{prop.id}", decision="error",
                     reason=error, level=int(prop.level), approval_id=prop.id, run_id=prop.run_id,
                     details={"tier": int(prop.tier), "requester_id": prop.requester_id,
                              "approver_id": prop.approver_id})
        audit.record_owner_job("executor", {"proposal_id": prop.id, "status": "failed"})
    except Exception as e:  # noqa: BLE001 - the status is what unblocks the thread; log the gap
        log.warning("audit for failed %s could not be written: %s", prop.id, type(e).__name__)
    return prop


def fail_interrupted(older_than_s: int = 300) -> list[str]:
    """Owner job (API start-up): proposals left 'approved' by a crash between the decision and the
    executor are marked failed ("execution_interrupted") instead of being sent late or never. The
    approver can ask for a new proposal; the requester's thread resumes as not sent."""
    with pg.connect_owner() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM ops.proposals WHERE status = 'approved'"
            " AND decided_at < now() - make_interval(secs => %s)", (int(older_than_s),)).fetchall()]
    return [mark_failed(i, "execution_interrupted").id for i in ids]
