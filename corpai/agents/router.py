"""App → graph router (architecture §12). Code, not a model: the UI names an app, the router finds
the agent serving it in config/agents.yaml, checks that the principal may start that agent
(policy.may_start) and returns the graph builder.

Milestone graphs live in their own packages (corpai/agents/legal, report, sales, hr) and expose
`build_<app>_graph()` or `build_graph()`; until a package exists its app answers "arrives in
milestone Mx". Tests may add apps with register_app().
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from typing import Any, Callable

from corpai.policy import load_agents, may_start
from corpai.types import Principal

_APP_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# app -> (module that will hold its graph, milestone)
MILESTONE_APPS: dict[str, tuple[str, str]] = {
    "legal_review": ("corpai.agents.legal", "M2"),
    "report": ("corpai.agents.report", "M3"),
    "sales_profile": ("corpai.agents.sales", "M4"),
    "hr_headcount": ("corpai.agents.hr", "M4"),
    "hr_screening": ("corpai.agents.hr", "M4"),
}


class RouterError(Exception):
    status = 400
    code = "bad_request"


class UnknownApp(RouterError):
    pass


class AppNotReady(RouterError):
    pass


class StartDenied(RouterError):
    status = 403
    code = "forbidden"


@dataclass(frozen=True)
class AppRoute:
    app: str
    agent: str
    builder: Callable[[], Any]  # -> uncompiled StateGraph
    initial_state: Callable[[str], dict]


# Extra apps (tests, later milestones that prefer explicit registration): app -> (agent, builder, init)
_REGISTRY: dict[str, tuple[str, Callable[[], Any], Callable[[str], dict]]] = {}


def register_app(app: str, *, agent: str, builder: Callable[[], Any],
                 initial_state: Callable[[str], dict] | None = None) -> None:
    if not _APP_RE.match(app):
        raise ValueError("app names are lower-case identifiers")
    _REGISTRY[app] = (agent, builder, initial_state or (lambda text: {"question": text}))


def unregister_app(app: str) -> None:
    _REGISTRY.pop(app, None)


def agent_for_app(app: str) -> str | None:
    if app in _REGISTRY:
        return _REGISTRY[app][0]
    for name, spec in load_agents().items():
        if app in spec.apps:
            return name
    return None


def _builder(app: str) -> tuple[Callable[[], Any], Callable[[str], dict]]:
    if app in _REGISTRY:
        _, b, init = _REGISTRY[app]
        return b, init
    if app == "qa":
        from corpai.agents import qa
        return qa.build_graph, qa.initial_state
    if app in MILESTONE_APPS:
        module, milestone = MILESTONE_APPS[app]
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError as e:
            if e.name is not None and module.startswith(e.name):
                raise AppNotReady(f"the {app} app arrives in milestone {milestone}") from None
            raise
        build = getattr(mod, f"build_{app}_graph", None) or getattr(mod, "build_graph", None)
        if build is None:
            raise AppNotReady(f"the {app} app arrives in milestone {milestone}")
        init = getattr(mod, f"{app}_initial_state", None) or getattr(mod, "initial_state", None) \
            or (lambda text: {"question": text})
        return build, init
    raise UnknownApp(f"unknown app {app!r}")


def resolve(p: Principal, app: Any) -> AppRoute:
    """The route for `app` if `p` may start its agent. Raises UnknownApp, StartDenied, AppNotReady
    (in that order: someone who may not start an app learns nothing about its readiness)."""
    if not isinstance(app, str) or not _APP_RE.match(app):
        raise UnknownApp("app must be one of the apps listed by /api/me")
    agent = agent_for_app(app)
    if agent is None:
        raise UnknownApp(f"unknown app {app!r}")
    dec = may_start(p, agent)
    if not dec.allow:
        raise StartDenied(f"your role may not start {app} ({dec.rule_id})")
    build, init = _builder(app)
    return AppRoute(app=app, agent=agent, builder=build, initial_state=init)


def app_ready(app: str) -> bool:
    """True when the app's graph exists today. /api/me lists only these, so the UI never offers an
    app whose every run would answer "arrives in milestone Mx"."""
    try:
        _builder(app)
    except RouterError:
        return False
    return True


def buildable_apps() -> dict[str, Callable[[], Any]]:
    """Every app whose graph can be built right now (for the Rule-of-Two test)."""
    apps = {a for spec in load_agents().values() for a in spec.apps} | set(_REGISTRY)
    out = {}
    for app in sorted(apps):
        try:
            out[app] = _builder(app)[0]
        except RouterError:
            continue
    return out


_compiled: dict[tuple[str, int], Any] = {}


def compiled_graph(route: AppRoute, checkpointer: Any) -> Any:
    """Compile once per (app, checkpointer); compiled graphs are stateless and reusable."""
    key = (route.app, id(checkpointer))
    g = _compiled.get(key)
    if g is None or getattr(g, "checkpointer", None) is not checkpointer:
        g = route.builder().compile(checkpointer=checkpointer)
        _compiled[key] = g
    return g


def forget_compiled(checkpointer: Any) -> None:
    for key in [k for k in _compiled if k[1] == id(checkpointer)]:
        _compiled.pop(key, None)
