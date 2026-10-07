"""effective_groups(): the §5 group table."""

from __future__ import annotations

import pytest

from corpai.identity.groups import effective_groups
from tests.policy.fake_personas import persona


def _row(**kw):
    base = {"user_id": "u0100", "dept": "sales", "role": "employee", "active": True,
            "memberships": [], "grants": [], "direct_reports": []}
    base.update(kw)
    return base


def test_employee():
    assert effective_groups(_row()) == {"all", "dept:sales", "user:u0100"}


def test_manager_gets_head_and_mgr_of():
    g = effective_groups(_row(role="manager", direct_reports=["u0101", "u0102"]))
    assert g == {"all", "dept:sales", "head:sales", "user:u0100", "mgr-of:u0101", "mgr-of:u0102"}


def test_ceo_is_head_of_exec():
    g = effective_groups(_row(user_id="u0001", dept="exec", role="ceo", direct_reports=["u0003"]))
    assert g == {"all", "dept:exec", "head:exec", "role:ceo", "user:u0001", "mgr-of:u0003"}


@pytest.mark.parametrize("role", ["platform_admin", "security_auditor"])
def test_ops_roles_are_not_heads(role):
    g = effective_groups(_row(dept="it", role=role))
    assert g == {"all", "dept:it", f"role:{role}", "user:u0100"}
    assert not any(x.startswith("head:") for x in g)


def test_memberships_and_grants_pass_through():
    g = effective_groups(_row(memberships=["proj:P-01", "req:R-01"], grants=["grant:crm:A-0012"]))
    assert {"proj:P-01", "req:R-01", "grant:crm:A-0012"} <= g


def test_service_account_is_not_a_person():
    g = effective_groups(_row(user_id="svc:metrics", dept="it", role="service"))
    assert g == {"svc:metrics"}
    assert "all" not in g and not any(x.startswith(("dept:", "user:")) for x in g)


def test_inactive_has_no_groups():
    assert effective_groups(_row(active=False, role="ceo", memberships=["proj:P-01"])) == frozenset()
    assert effective_groups({"user_id": "u1", "dept": "sales", "role": "employee"}) == frozenset()  # missing = inactive


@pytest.mark.parametrize("bad", [["a,b"], [""]])
def test_group_names_with_commas_rejected(bad):
    # a comma would split into two groups in app.groups and widen access
    with pytest.raises(ValueError):
        effective_groups(_row(memberships=bad))


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        effective_groups(_row(role="superuser"))


def test_fake_persona_groups():
    assert persona("eng_head").groups == {
        "all", "dept:eng", "head:eng", "user:u0005", "req:R-01",
        "mgr-of:u0006", "mgr-of:u0013", "mgr-of:u0016"}
    assert persona("hr_staff").groups == {"all", "dept:hr", "user:u0012", "req:R-01"}
