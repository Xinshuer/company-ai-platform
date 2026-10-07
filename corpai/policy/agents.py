"""Agent allowlists from config/agents.yaml: which tools an agent holds and who may start it."""

from __future__ import annotations

from typing import Any

from corpai.config import load_yaml
from corpai.policy.catalogue import TOOL_CATALOGUE
from corpai.policy.model import AgentSpec
from corpai.types import Decision, Principal, Role

# Department membership never admits these roles to an agent; they must be listed by role.
_DEPT_EXCLUDED = frozenset({Role.PLATFORM_ADMIN, Role.SECURITY_AUDITOR, Role.SERVICE})


class AgentConfigError(ValueError):
    pass


def _spec(name: str, raw: dict[str, Any]) -> AgentSpec:
    start = raw.get("start") or {}
    tools = frozenset(raw.get("tools") or ())
    for t in tools:
        domain, _, tool = t.partition(".")
        if tool not in TOOL_CATALOGUE.get(domain, {}):
            raise AgentConfigError(f"agent {name}: unknown tool {t!r}")
    try:
        roles = frozenset(Role(r) for r in start.get("roles") or ())
    except ValueError as e:
        raise AgentConfigError(f"agent {name}: {e}") from None
    if Role.SERVICE in roles:
        raise AgentConfigError(f"agent {name}: service accounts cannot start agents")
    return AgentSpec(
        name=name,
        description=str(raw.get("description", "")),
        apps=tuple(raw.get("apps") or ()),
        start_roles=roles,
        start_depts=frozenset(str(d) for d in start.get("depts") or ()),
        tools=tools,
        purpose=raw.get("purpose"),
        subagent_of=raw.get("subagent_of"),
    )


_parsed: tuple[Any, dict[str, AgentSpec]] | None = None  # (the yaml object, specs)


def load_agents() -> dict[str, AgentSpec]:
    """name -> AgentSpec, validated. Re-parsed whenever load_yaml's cache is cleared (reload_settings())."""
    global _parsed
    raw = load_yaml("agents") or {}
    if _parsed is None or _parsed[0] is not raw:  # identity, not id(): ids are reused after GC
        specs = {name: _spec(name, body or {}) for name, body in (raw.get("agents") or {}).items()}
        _parsed = (raw, specs)
    return dict(_parsed[1])


def agent_spec(name: str) -> AgentSpec | None:
    return load_agents().get(name)


def may_start(p: Principal, agent: str) -> Decision:
    if not p.active:
        return Decision(False, "deny-inactive", "the account is deactivated")
    spec = agent_spec(agent)
    if spec is None:
        return Decision(False, "agent-unknown", f"no agent named {agent!r}")
    if p.role in spec.start_roles:
        return Decision(True, "agent-start-role", f"role {p.role} may start {agent}")
    if p.role not in _DEPT_EXCLUDED and str(p.dept) in spec.start_depts:
        return Decision(True, "agent-start-dept", f"dept {p.dept} may start {agent}")
    return Decision(False, "agent-start-denied", f"{p.role} in {p.dept} may not start {agent}")


def apps_for(p: Principal) -> list[str]:
    """UI apps the principal may open (for /api/me)."""
    out: list[str] = []
    for name, spec in load_agents().items():
        if spec.apps and may_start(p, name).allow:
            out.extend(a for a in spec.apps if a not in out)
    return out
