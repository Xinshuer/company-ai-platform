"""The router is code: unknown apps fail, may_start is checked before anything else, apps of later
milestones say when they arrive."""

from __future__ import annotations

import pytest

from corpai.agents import router
from corpai.types import Dept, Level, Principal, Role


def _p(role: Role, dept: Dept, *, active: bool = True) -> Principal:
    return Principal(user_id="u0999", name="x", email="x@abctech.example", dept=dept, role=role, title="t",
                     manager_id=None, clearance=Level.L3, groups=frozenset({"all"}), active=active)


def test_qa_resolves_for_staff():
    r = router.resolve(_p(Role.EMPLOYEE, Dept.SALES), "qa")
    assert r.agent == "qa" and r.app == "qa"
    assert r.initial_state("hi")["question"] == "hi"


@pytest.mark.parametrize("app", ["nope", "", "QA", "qa; drop", None, 3, "sales_web"])
def test_unknown_or_malformed_apps(app):
    with pytest.raises(router.UnknownApp):
        router.resolve(_p(Role.EMPLOYEE, Dept.SALES), app)


def test_start_denied_before_readiness_is_revealed():
    with pytest.raises(router.StartDenied) as e:
        router.resolve(_p(Role.EMPLOYEE, Dept.SALES), "legal_review")
    assert e.value.status == 403 and e.value.code == "forbidden"
    with pytest.raises(router.StartDenied):
        router.resolve(_p(Role.EMPLOYEE, Dept.SALES, active=False), "qa")


def test_milestone_apps_say_when_they_arrive(monkeypatch):
    import importlib

    real = importlib.import_module

    def missing(name, *a, **kw):
        if name.startswith("corpai.agents.") and name.split(".")[2] in ("legal", "report", "sales", "hr"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real(name, *a, **kw)

    monkeypatch.setattr(router.importlib, "import_module", missing)
    for p, app, ms in ((_p(Role.EMPLOYEE, Dept.LEGAL), "legal_review", "M2"), (_p(Role.CEO, Dept.EXEC), "report", "M3"),
                       (_p(Role.EMPLOYEE, Dept.SALES), "sales_profile", "M4"),
                       (_p(Role.EMPLOYEE, Dept.HR), "hr_screening", "M4")):
        with pytest.raises(router.AppNotReady, match=ms):
            router.resolve(p, app)


def test_registered_apps_still_need_may_start():
    router.register_app("b3_probe", agent="sales_profile", builder=lambda: None)
    try:
        assert router.resolve(_p(Role.EMPLOYEE, Dept.SALES), "b3_probe").agent == "sales_profile"
        with pytest.raises(router.StartDenied):
            router.resolve(_p(Role.EMPLOYEE, Dept.ENG), "b3_probe")
    finally:
        router.unregister_app("b3_probe")
