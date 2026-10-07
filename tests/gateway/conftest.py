"""Fixtures for the gateway tests: principals built directly, a fresh FakeLLM-backed gateway per
test, and owner-side readers for audit, trace and egress rows (the app role cannot read them)."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

import pytest

from corpai.db import pg as pgmod
from corpai.gateway import FakeLLM, ModelGateway, load_config
from corpai.ids import new_id
from corpai.types import Dept, Level, Principal, Role

_ROLE_GROUP = {Role.SECURITY_AUDITOR: "role:security_auditor", Role.PLATFORM_ADMIN: "role:platform_admin",
               Role.CEO: "role:ceo"}


def make_principal(user_id: str, role: Role = Role.EMPLOYEE, dept: Dept = Dept.SALES,
                   clearance: Level = Level.L3, groups: set[str] | None = None,
                   active: bool = True) -> Principal:
    if groups is None:
        groups = {"all", f"dept:{dept}", f"user:{user_id}"}
        if role in _ROLE_GROUP:
            groups.add(_ROLE_GROUP[role])
        if role == Role.MANAGER:
            groups.add(f"head:{dept}")
    return Principal(user_id=user_id, name=user_id, email=f"{user_id}@abctech.example", dept=dept,
                     role=role, title="t", manager_id=None, clearance=clearance,
                     groups=frozenset(groups), active=active)


@pytest.fixture
def mkp() -> Callable[..., Principal]:
    return make_principal


@pytest.fixture
def staff() -> Principal:
    return make_principal("u0031", Role.EMPLOYEE, Dept.SALES, Level.L3)


@pytest.fixture
def admin() -> Principal:
    return make_principal("u0067", Role.PLATFORM_ADMIN, Dept.IT, Level.L2)


@pytest.fixture
def fake() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def make_gw(pg, fake) -> Callable[..., ModelGateway]:
    """make_gw(local={...overrides}, external={...}, retry=RetryConfig(...)) -> ModelGateway."""

    def _make(*, backend=None, local: dict | None = None, external: dict | None = None, **top) -> ModelGateway:
        cfg = load_config()
        routes = dict(cfg.routes)
        from corpai.types import Route
        if local:
            routes[Route.LOCAL] = replace(routes[Route.LOCAL], **local)
        if external:
            routes[Route.EXTERNAL] = replace(routes[Route.EXTERNAL], **external)
        cfg = replace(cfg, routes=routes, **top)
        return ModelGateway(backend or fake, config=cfg)

    return _make


@pytest.fixture
def gw(make_gw) -> ModelGateway:
    return make_gw()


@pytest.fixture
def run_id() -> str:
    return new_id("run")


GAZETTEER = ["Anna Becker", "Jonas Weber", "Piotr Nowak"]


@pytest.fixture(autouse=True)
def _gazetteer_loaded():
    """The gateway fails closed (>= L3) while no person-name gazetteer is loaded; routing tests run
    with a small one, as the API does after loading it from the directory at startup."""
    from corpai.classify import names

    saved = names._state
    names.set_gazetteer(GAZETTEER)
    yield GAZETTEER
    names._state = saved


@pytest.fixture(autouse=True)
def _switches_off(pg):
    """Every test starts and ends with both outage switches off (they are global state)."""
    _set_switches(False, False)
    yield
    _set_switches(False, False)


def _set_switches(local_down: bool, external_down: bool) -> None:
    with pgmod.connect_owner() as c:
        c.execute("UPDATE ops.switches SET \"on\" = %s WHERE name = 'local_down'", (local_down,))
        c.execute("UPDATE ops.switches SET \"on\" = %s WHERE name = 'external_down'", (external_down,))


@pytest.fixture
def set_switches():
    return _set_switches


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    with pgmod.connect_owner() as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture
def audit_rows() -> Callable[[str], list[dict]]:
    return lambda run_id: owner_rows("SELECT * FROM audit.log WHERE run_id = %s ORDER BY seq", (run_id,))


@pytest.fixture
def egress_rows() -> Callable[[str | None], list[dict]]:
    def _rows(run_id: str | None = None) -> list[dict]:
        if run_id is None:
            return owner_rows("SELECT * FROM ops.egress_log ORDER BY id")
        return owner_rows("SELECT * FROM ops.egress_log WHERE run_id = %s ORDER BY id", (run_id,))
    return _rows


@pytest.fixture
def span_rows() -> Callable[[str], list[dict]]:
    return lambda run_id: owner_rows("SELECT * FROM trace.spans WHERE run_id = %s", (run_id,))
