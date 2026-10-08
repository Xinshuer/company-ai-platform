"""The result envelope every read tool returns (architecture §11) and the per-call wrapper.

    {"ok": true, "data": ..., "label": {"level", "dept", "trust", "sources"}, "denied": [{id, reason}],
     "masked_fields": [], "error": null}

label.level = max(level of every row returned, classify_text(serialized data)). A row only reaches
`data` after policy.decide() allowed it (RLS already filtered underneath); a requested id that is
not visible is listed in `denied` as "not found or no permission", so the caller can say "no
permission" instead of silently skipping, without learning whether the row exists.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable, Mapping

from mcp.server.mcpserver.exceptions import ToolError

from corpai import audit
from corpai.classify import classify_text
from corpai.mcp_servers._auth import AuthContext, authenticate
from corpai.policy import decide
from corpai.proposals.model import ProposalError
from corpai.types import TRUST_RANK, Action, Dept, Label, Level, Resource, Trust

log = logging.getLogger(__name__)

NOT_VISIBLE = "not found or no permission"
MAX_SOURCES = 200


class ToolArgError(ValueError):
    """Bad arguments. The message is ours (fixed text naming the argument), never the value."""


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Mapping):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [jsonable(x) for x in v]
    return v


def label_of(row: Mapping[str, Any], *, kind: str, rid: str | None) -> Label:
    """The row's §4 label columns. Missing or malformed → L4 with no ACL (fail closed: nobody passes)."""
    try:
        level = Level(int(row["level"]))
        acl = frozenset(row["acl_groups"] or ())
        trust = Trust(row.get("trust") or "internal")
    except (KeyError, TypeError, ValueError):
        return Label(level=Level.L4, sources=(f"{kind}:{rid}",))
    dept = None
    try:
        dept = Dept(row["dept"]) if row.get("dept") else None
    except ValueError:
        dept = None
    return Label(level=level, dept=dept, owner_id=row.get("owner_id"), acl_groups=acl, trust=trust,
                 subject_ids=frozenset(row.get("subject_ids") or ()), sources=(f"{kind}:{rid}",))


class Collector:
    """Gathers the rows of one tool call: policy check per row, label, denials, field masks."""

    def __init__(self, auth: AuthContext):
        self.auth = auth
        self.levels: list[int] = []
        self.trusts: list[Trust] = []
        self.depts: set[str | None] = set()
        self.sources: list[str] = []
        self.subjects: set[str] = set()  # GDPR data subjects of the rows returned (for the trace span)
        self.denied: list[dict] = []
        self.masked: set[str] = set()
        self.obligations: set[str] = set()
        self.public = True  # every row so far is published (L1, acl all): classify with public=True
        self.resource: str | None = None  # "kind:id" for the audit row; set by tools after validating the id

    def row(self, kind: str, rid: str | None, row: Mapping[str, Any], fields: Iterable[str] | None = None, *,
            action: Action = Action.READ, attrs: Mapping[str, Any] | None = None) -> dict | None:
        """decide() on the concrete row; returns the allowed fields or None (and records the denial)."""
        label = label_of(row, kind=kind, rid=rid)
        dec = decide(self.auth.principal, action, Resource(kind=kind, id=rid, label=label, attrs=dict(attrs or {})))
        if not dec.allow:
            self.deny(f"{kind}:{rid}", dec.reason)
            return None
        keys = list(fields) if fields is not None else [k for k in row.keys() if k not in _LABEL_COLS]
        out = {}
        for k in keys:
            if k in dec.field_mask:
                self.masked.add(k)
                continue
            out[k] = jsonable(row.get(k))
        self.add_label(label)
        self.obligations |= set(dec.obligations)
        return out

    def add_label(self, label: Label) -> None:
        self.levels.append(int(label.level))
        self.trusts.append(label.trust)
        self.depts.add(str(label.dept) if label.dept else None)
        self.sources.extend(label.sources)
        self.subjects.update(label.subject_ids)
        if not (label.level == Level.L1 and "all" in label.acl_groups):
            self.public = False

    def add_level(self, level: int, *, source: str | None = None, trust: Trust = Trust.INTERNAL,
                  dept: str | None = None, public: bool = False) -> None:
        self.levels.append(int(level))
        self.trusts.append(trust)
        self.depts.add(dept)
        if source:
            self.sources.append(source)
        if not public:
            self.public = False

    def deny(self, rid: str, reason: str = NOT_VISIBLE) -> None:
        self.denied.append({"id": rid, "reason": reason})

    def envelope(self, data: Any, *, ok: bool = True, error: str | None = None, message: str | None = None) -> dict:
        data = jsonable(data)
        text = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
        level = max([*self.levels, int(classify_text(text, public=self.public and bool(self.levels)).level)])
        trust = min(self.trusts, key=lambda t: TRUST_RANK[t]) if self.trusts else Trust.INTERNAL
        depts = {d for d in self.depts}
        env = {
            "ok": ok,
            "data": data,
            "label": {
                "level": int(level),
                "dept": next(iter(depts)) if len(depts) == 1 else None,
                "trust": str(trust),
                "sources": list(dict.fromkeys(self.sources))[:MAX_SOURCES],
                "subject_ids": sorted(self.subjects),
            },
            "denied": self.denied,
            "masked_fields": sorted(self.masked),
            "error": error,
        }
        if message:
            env["message"] = message
        return env

    def error(self, code: str, message: str) -> dict:
        return self.envelope(None, ok=False, error=code, message=message)


_LABEL_COLS = frozenset({"level", "dept", "owner_id", "acl_groups", "trust", "subject_ids"})


async def run_tool(ctx: Any, domain: str, tool: str, body: Callable[[Collector], Awaitable[dict]]) -> dict:
    """authenticate → body(collector) → audit tool_call. A ToolArgError becomes an ok=false
    envelope; any other failure is audited as an error and surfaces as a fixed message."""
    auth = await authenticate(ctx, domain=domain, tool=tool)
    col = Collector(auth)
    try:
        env = await body(col)
    except ToolArgError as e:
        env = Collector(auth).error("bad_arguments", str(e))
    except ProposalError as e:  # fixed messages, no parameter values
        env = Collector(auth).error(e.code, str(e))
    except Exception as e:
        log.error("%s.%s failed: %s", domain, tool, type(e).__name__)
        await _audit(auth, col.resource, decision="error", reason=type(e).__name__, level=None, details={})
        raise ToolError(f"{domain}.{tool} failed; try again or ask the platform team") from None
    await _audit(auth, col.resource, decision="allow", reason="ok" if env.get("ok") else str(env.get("error")),
                 level=env["label"]["level"],
                 details={"denied": len(env.get("denied") or ()), "sources": len(env["label"]["sources"])})
    return env


async def _audit(auth: AuthContext, resource: str | None, *, decision: str, reason: str, level: int | None,
                 details: dict) -> None:
    details = {"jti": auth.jti, "rule_id": auth.rule_id, **details}
    await audit.arecord("tool_call", actor=auth.principal.user_id, act_chain=auth.act_chain,
                        action=f"{auth.domain}.{auth.tool}", resource=resource, decision=decision, reason=reason,
                        level=level, run_id=auth.run_id, details=details)
