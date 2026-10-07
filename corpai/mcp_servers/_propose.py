"""Shared path of the write tools (tier 2/3): they never write business tables.

A write tool validates its arguments, checks decide(PROPOSE) on the concrete target row (or on the
label the new row would get), and files a proposal. The envelope carries
{"proposal_id", "status": "pending", "tier"}; a person approves it later (corpai.proposals).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from corpai import proposals
from corpai.mcp_servers._envelope import NOT_VISIBLE, Collector, label_of
from corpai.policy import decide
from corpai.types import Action, Label, Level, Resource


def may_propose_on(col: Collector, kind: str, rid: str | None, row: Mapping[str, Any] | None) -> bool:
    """PROPOSE on an existing row the caller can see. Invisible or refused → denied entry, False."""
    if row is None:
        col.deny(f"{kind}:{rid}", NOT_VISIBLE)
        return False
    return col.row(kind, rid, row, fields=[], action=Action.PROPOSE) is not None


def may_propose_new(col: Collector, kind: str, label: Label) -> bool:
    """PROPOSE a new row: decided on the label the executor will give it (§5.1)."""
    dec = decide(col.auth.principal, Action.PROPOSE, Resource(kind=kind, id=None, label=label))
    if not dec.allow:
        col.deny(f"{kind}:new", dec.reason)
        return False
    return True


def refused(col: Collector) -> dict:
    return col.error("not_found_or_denied", "no permission for this change, or the record does not exist")


async def file_proposal(col: Collector, *, tool: str, params: Mapping[str, Any], summary: str,
                        level_floor: Level | int = Level.L1, subject_ids: Iterable[str] = ()) -> dict:
    auth = col.auth
    prop = await proposals.create(auth.principal, agent=auth.agent, run_id=auth.run_id, domain=auth.domain, tool=tool,
                                  params=params, summary=summary, level_floor=level_floor, subject_ids=subject_ids)
    col.add_label(label_of({"level": int(prop.level), "acl_groups": list(prop.acl_groups)}, kind="proposal",
                           rid=prop.id))
    col.resource = col.resource or f"proposal:{prop.id}"
    env = col.envelope({"proposal_id": prop.id, "status": prop.status, "tier": int(prop.tier)})
    env.update({"proposal_id": prop.id, "status": prop.status, "tier": int(prop.tier)})
    return env
