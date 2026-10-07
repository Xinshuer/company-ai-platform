"""In-memory stand-ins for the runtime's services, so runtime rules are tested without a database."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from corpai.agents import RunContext, Services
from corpai.ids import new_id
from corpai.types import Dept, Level, Principal, Role


def principal(user_id: str = "u0011", *, clearance: Level = Level.L3, active: bool = True,
              email: str = "ctx-marker-7f3a@kestrelbyte.example") -> Principal:
    return Principal(user_id=user_id, name="Test User", email=email, dept=Dept.SALES, role=Role.EMPLOYEE,
                     title="Account Executive", manager_id=None, clearance=clearance,
                     groups=frozenset({"all", "dept:sales", f"user:{user_id}", "grp-marker-91c2"}), active=active)


@dataclass
class NullSpan:
    name: str
    kind: str
    level: Level
    parent_id: str | None
    input: str | None
    span_id: str = field(default_factory=lambda: new_id("span"))
    attrs: dict = field(default_factory=dict)
    output: str | None = None

    def set_level(self, level):
        self.level = max(self.level, Level(level))

    def set_attr(self, k, v):
        self.attrs[k] = v

    def set_output(self, text):
        self.output = text


class NullTracer:
    def __init__(self):
        self.spans: list[NullSpan] = []

    @asynccontextmanager
    async def span(self, name, *, kind, run_id, principal, level, parent_id=None, input=None):
        sp = NullSpan(name=name, kind=kind, level=Level(level), parent_id=parent_id, input=input)
        self.spans.append(sp)
        yield sp


class FakeGateway:
    """Routes like the real one (external only at L1/L2) and records every request."""

    def __init__(self, content_level: Level = Level.L1, text: str = "ok", tool_calls: list | None = None):
        self.requests: list[Any] = []
        self.content_level = content_level
        self.text = text
        self.tool_calls = tool_calls or []

    async def chat(self, req):
        self.requests.append(req)
        level = Level(max(int(req.hwm), int(self.content_level)))
        route = "external" if level <= Level.L2 else "local"
        return SimpleNamespace(text=self.text, tool_calls=list(self.tool_calls), route=route, level=level,
                               model="fake", usage={}, latency_ms=0, audit_seq=0)


class FakeMcp:
    def __init__(self, envelopes: list | None = None):
        self.calls: list[dict] = []
        self.envelopes = list(envelopes or [])

    async def call(self, **kw):
        self.calls.append(kw)
        return self.envelopes.pop(0)


def context(*, gateway=None, mcp=None, tracer=None, p: Principal | None = None, agent: str = "qa",
            user_level: Level = Level.L1) -> RunContext:
    return RunContext(principal=p or principal(), run_id=new_id("run"), thread_id=new_id("thr"), agent=agent,
                      services=Services(gateway=gateway or FakeGateway(), mcp=mcp or FakeMcp(),
                                        tracer=tracer or NullTracer()),
                      user_level=user_level)
