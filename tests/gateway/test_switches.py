"""Outage switches (rule 4): local_down never reroutes L3/L4 to external; only the platform admin
flips a switch, and every attempt is audited."""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from corpai.db import pg as pgmod
from corpai.gateway import ChatRequest, LocalUnavailable, ops
from corpai.types import Dept, Level, Role, Route

pytestmark = [pytest.mark.anyio, pytest.mark.security]

PUBLIC_Q = "What is deferred revenue?"
CANARY = "KB-CANARY-77AA88BB"


def req(principal, text, *, hwm, run_id, tools=None) -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": text}], principal=principal, hwm=hwm,
                       purpose="qa.answer", run_id=run_id, agent="qa", tools=tools)


def _egress_count() -> int:
    with pgmod.connect_owner() as c:
        return c.execute("SELECT count(*) AS n FROM ops.egress_log").fetchone()["n"]


async def test_local_down_l3_errors_with_zero_egress(gw, fake, staff, run_id, set_switches, audit_rows):
    set_switches(True, False)
    before = _egress_count()
    with pytest.raises(LocalUnavailable):
        await gw.chat(req(staff, f"Contract excerpt {CANARY}", hwm=Level.L3, run_id=run_id))
    assert fake.calls == []
    assert _egress_count() == before
    (row,) = audit_rows(run_id)
    assert (row["event"], row["decision"], row["reason"], row["route"]) == ("model_call", "deny", "local_down", "local")


async def test_local_down_l1_still_goes_external(gw, fake, staff, run_id, set_switches):
    set_switches(True, False)
    res = await gw.chat(req(staff, PUBLIC_Q, hwm=Level.L1, run_id=run_id))
    assert res.route == Route.EXTERNAL
    assert fake.calls[-1]["route"] == "external"


async def test_both_down_l1_errors(gw, fake, staff, run_id, set_switches):
    """external_down sends L1 to local; local is down too, so the safe answer is an error."""
    set_switches(True, True)
    with pytest.raises(LocalUnavailable):
        await gw.chat(req(staff, PUBLIC_Q, hwm=Level.L1, run_id=run_id))
    assert fake.calls == []


async def test_local_down_after_dlp_upgrade_errors(gw, fake, staff, run_id, set_switches):
    set_switches(True, False)
    tools = [{"type": "function", "function": {"name": "f", "description": "IBAN DE89 3704 0044 0532 0130 00",
                                               "parameters": {"type": "object"}}}]
    before = _egress_count()
    with pytest.raises(LocalUnavailable):
        await gw.chat(req(staff, PUBLIC_Q, hwm=Level.L1, run_id=run_id, tools=tools))
    assert fake.calls == [] and _egress_count() == before


async def test_chaos_batch_of_l3_requests(gw, fake, mkp, run_id, set_switches):
    """design-review 二.2 chaos test: switch local off, fire a batch of L3 requests, external egress = 0."""
    set_switches(True, False)
    before = _egress_count()
    people = [mkp(f"u00{40 + i}", dept=d) for i, d in enumerate([Dept.SALES, Dept.LEGAL, Dept.HR, Dept.FINANCE])]
    texts = ["Customer contact anna.k@nordhafen-logistik.example", "Salary of the CFO is 180000 EUR",
             f"Litigation memo {CANARY}", "IBAN DE89 3704 0044 0532 0130 00"]
    jobs = [gw.chat(req(people[i % 4], texts[i % 4], hwm=Level.L1 if i % 2 else Level.L3, run_id=f"{run_id}-{i}"))
            for i in range(20)]
    results = await asyncio.gather(*jobs, return_exceptions=True)
    assert all(isinstance(r, LocalUnavailable) for r in results), results
    assert fake.calls == []
    assert _egress_count() == before


async def test_switch_read_and_admin_set(admin, staff, set_switches):
    assert await ops.get_switches() == {"local_down": False, "external_down": False}
    row = await ops.set_switch(admin, "local_down", True, "GPU maintenance window")
    assert row["on"] is True and row["updated_by"] == admin.user_id
    assert (await ops.get_switches())["local_down"] is True
    rows = await ops.read_switches()
    assert {r["name"]: r["reason"] for r in rows}["local_down"] == "GPU maintenance window"
    with pgmod.connect_owner() as c:
        a = c.execute("SELECT * FROM audit.log WHERE event = 'outage_switch' ORDER BY seq DESC LIMIT 1").fetchone()
    assert (a["actor"], a["decision"], a["resource"], a["details"]) == (admin.user_id, "allow", "switch:local_down",
                                                                         {"on": True})


@pytest.mark.parametrize("who", ["staff", "ceo", "auditor", "inactive_admin"])
async def test_non_admin_cannot_set_switch(mkp, who):
    p = {
        "staff": mkp("u0031"),
        "ceo": mkp("u0001", Role.CEO, Dept.EXEC, Level.L4),
        "auditor": mkp("u0068", Role.SECURITY_AUDITOR, Dept.IT, Level.L2),
        "inactive_admin": mkp("u0067", Role.PLATFORM_ADMIN, Dept.IT, Level.L2, active=False),
    }[who]
    with pytest.raises(PermissionError):
        await ops.set_switch(p, "local_down", True, "trying")
    assert (await ops.get_switches())["local_down"] is False
    with pgmod.connect_owner() as c:
        a = c.execute("SELECT * FROM audit.log WHERE event = 'outage_switch' ORDER BY seq DESC LIMIT 1").fetchone()
    assert (a["actor"], a["decision"]) == (p.user_id, "deny")


@pytest.mark.parametrize("groups,role", [
    ({"all", "user:u0031"}, "employee"),                        # plain staff
    ({"all", "user:u0067"}, "platform_admin"),                  # role claimed, group missing
    ({"all", "role:platform_admin"}, "employee"),               # group without the role
])
def test_sql_set_switch_requires_admin_role_and_group(pg, mkp, groups, role):
    """The SQL function refuses on its own, whatever the Python wrapper does."""
    p = mkp("u0031", Role(role), Dept.IT, Level.L2, groups=groups)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(p) as conn:
            conn.execute("SELECT * FROM ops.set_switch('local_down', true, 'x')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.connect_app() as conn:  # no principal at all
            conn.execute("SELECT * FROM ops.set_switch('local_down', true, 'x')")


def test_app_role_cannot_touch_switch_table(pg, admin):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(admin) as conn:
            conn.execute("UPDATE ops.switches SET \"on\" = true")


async def test_set_switch_validates_input(admin):
    for args in [("nope", True, "r"), ("local_down", "yes", "r"), ("local_down", True, "  ")]:
        with pytest.raises(ValueError):
            await ops.set_switch(admin, *args)
