"""Fail-closed details of ModelGateway.chat that review found missing:

- no person-name gazetteer loaded -> a prompt naming a person is not routed external;
- hidden-character secrets are scrubbed, and a secret classify_text still sees is refused;
- an outage switch flipped while a call waits for a slot, or between retries, stops that call;
- a cancelled call refunds its budget reservation and leaves a model_call audit row.
"""

from __future__ import annotations

import asyncio

import pytest

from corpai.classify import names
from corpai.db import pg as pgmod
from corpai.gateway import (
    ChatRequest,
    ExternalUnavailable,
    LocalUnavailable,
    RetryableBackendError,
    RetryConfig,
    SecretDetected,
    ops,
)
from corpai.gateway import gateway as gwmod
from corpai.types import Dept, Level, Route

pytestmark = [pytest.mark.anyio, pytest.mark.security]

NAMED = "Draft a follow-up email to Jonas Becker at Nordhafen Logistik about the renewal."
PUBLIC_Q = "What is deferred revenue?"


def req(principal, text, *, run_id, hwm=Level.L1, purpose="qa.answer") -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": text}], principal=principal, hwm=hwm,
                       purpose=purpose, run_id=run_id, agent="qa", max_tokens=50)


def _used(dept: str) -> int:
    with pgmod.connect_owner() as c:
        row = c.execute("SELECT tokens_used FROM ops.budgets WHERE dept = %s AND day = ops.budget_day()",
                        (dept,)).fetchone()
    return 0 if row is None else int(row["tokens_used"])


# ---- gazetteer ---------------------------------------------------------------------------------

@pytest.fixture
def no_gazetteer():
    saved = names._state
    names.set_gazetteer([])
    yield
    names._state = saved


async def test_without_a_gazetteer_a_named_person_is_not_sent_external(gw, fake, staff, run_id, no_gazetteer,
                                                                        audit_rows, egress_rows):
    res = await gw.chat(req(staff, NAMED, run_id=run_id))
    assert res.route == Route.LOCAL and res.level >= Level.L3
    assert fake.calls[-1]["route"] == "local"
    assert egress_rows(run_id) == []
    rd = [r for r in audit_rows(run_id) if r["event"] == "route_decision"]
    assert rd and rd[0]["reason"] == "gazetteer_unloaded" and "gazetteer_unloaded" in rd[0]["details"]["findings"]


async def test_without_a_gazetteer_even_public_questions_stay_local(gw, fake, staff, run_id, no_gazetteer):
    """The gateway cannot tell a name from an ordinary word without the gazetteer: unsure -> higher."""
    assert (await gw.chat(req(staff, PUBLIC_Q, run_id=run_id))).route == Route.LOCAL


async def test_with_the_gazetteer_the_named_person_is_l3(gw, fake, staff, run_id):
    names.set_gazetteer(["Jonas Becker"])  # restored by the autouse conftest fixture
    res = await gw.chat(req(staff, NAMED, run_id=run_id))
    assert (res.route, res.level) == (Route.LOCAL, Level.L3)
    assert (await gw.chat(req(staff, PUBLIC_Q, run_id=run_id))).route == Route.EXTERNAL


# ---- secrets -----------------------------------------------------------------------------------

async def test_zero_width_key_never_reaches_the_backend(gw, fake, staff, run_id):
    key = "sk-abcdefghij​klmnopqrstuvwxyz0123"
    await gw.chat(req(staff, f"use token {key} for the call", run_id=run_id))
    sent = repr(fake.calls[-1]["messages"])
    assert "klmnopqrstuvwxyz0123" not in sent and "abcdefghij" not in sent
    assert "[REDACTED:openai_key]" in sent


async def test_a_secret_left_after_scrubbing_is_refused(gw, fake, staff, run_id, monkeypatch, audit_rows):
    """Defence in depth: if scrub_secrets ever misses what classify_text calls a secret, refuse."""
    monkeypatch.setattr(gwmod, "scrub_secrets", lambda text: (text, 0))
    with pytest.raises(SecretDetected):
        await gw.chat(req(staff, "token sk-abcdefghijklmnopqrstuvwxyz0123", run_id=run_id))
    assert fake.calls == []
    (row,) = audit_rows(run_id)
    assert (row["decision"], row["reason"]) == ("deny", "secret_unscrubbed")
    assert "sk-abc" not in repr(row)


# ---- outage switches re-read after waiting ---------------------------------------------------------

async def test_local_down_flipped_while_queued_stops_the_queued_call(make_gw, fake, staff, run_id, set_switches,
                                                                     audit_rows):
    gw = make_gw(local={"max_concurrency": 1, "queue_timeout_s": 5})
    started, release = asyncio.Event(), asyncio.Event()

    async def hold(messages, *, route, json_mode):
        started.set()
        await release.wait()
        return "first answer"

    fake.register("hold.", hold)
    first = asyncio.ensure_future(gw.chat(req(staff, PUBLIC_Q, run_id=run_id, hwm=Level.L3, purpose="hold.x")))
    await started.wait()
    second = asyncio.ensure_future(gw.chat(req(staff, PUBLIC_Q, run_id=run_id + "-q", hwm=Level.L3,
                                               purpose="qa.answer")))
    await asyncio.sleep(0.2)  # the second call is now waiting for the only local slot
    set_switches(True, False)
    release.set()
    assert (await first).text == "first answer"  # already running: allowed to finish
    with pytest.raises(LocalUnavailable):
        await second
    assert [c["purpose"] for c in fake.calls] == ["hold.x"]
    rows = audit_rows(run_id + "-q")
    assert [(r["decision"], r["reason"]) for r in rows] == [("deny", "local_down")]


async def test_switch_flipped_between_retries_stops_the_retry(make_gw, fake, staff, run_id, set_switches,
                                                              audit_rows):
    gw = make_gw(retry=RetryConfig(max_attempts=3, base_delay_s=0.01, max_delay_s=0.01))
    attempts = []

    def flaky(messages, *, route, json_mode):
        attempts.append(route)
        set_switches(True, False)  # the admin switches local off during the first attempt
        raise RetryableBackendError("503", status=503)

    fake.register("flaky.", flaky)
    used = _used(str(staff.dept))
    with pytest.raises(LocalUnavailable):
        await gw.chat(req(staff, PUBLIC_Q, run_id=run_id, hwm=Level.L3, purpose="flaky.x"))
    assert attempts == ["local"]  # no second attempt
    assert _used(str(staff.dept)) == used  # reservation refunded
    (row,) = [r for r in audit_rows(run_id) if r["event"] == "model_call"]
    assert (row["decision"], row["reason"]) == ("deny", "local_down")


async def test_external_down_flipped_while_queued_stops_the_external_call(make_gw, fake, staff, run_id,
                                                                          set_switches, egress_rows):
    gw = make_gw(external={"max_concurrency": 1, "queue_timeout_s": 5})
    started, release = asyncio.Event(), asyncio.Event()

    async def hold(messages, *, route, json_mode):
        started.set()
        await release.wait()
        return "ok"

    fake.register("hold.", hold)
    first = asyncio.ensure_future(gw.chat(req(staff, PUBLIC_Q, run_id=run_id, purpose="hold.x")))
    await started.wait()
    second = asyncio.ensure_future(gw.chat(req(staff, PUBLIC_Q, run_id=run_id + "-q")))
    await asyncio.sleep(0.2)
    set_switches(False, True)
    release.set()
    await first
    with pytest.raises(ExternalUnavailable):
        await second
    assert egress_rows(run_id + "-q") == []  # nothing logged as sent, nothing sent


# ---- cancellation --------------------------------------------------------------------------------

@pytest.mark.parametrize("hwm,route", [(Level.L3, "local"), (Level.L1, "external")])
async def test_cancelled_call_refunds_and_is_audited(gw, fake, mkp, run_id, audit_rows, egress_rows, hwm, route):
    p = mkp("u0077", dept=Dept.FINANCE)
    await gw.chat(req(p, PUBLIC_Q, run_id=run_id + "-warm", hwm=hwm))  # today's row exists
    used = _used("finance")
    started = asyncio.Event()

    async def hang(messages, *, route, json_mode):
        started.set()
        await asyncio.sleep(30)
        return "never"

    fake.register("hang.", hang)
    task = asyncio.ensure_future(gw.chat(req(p, PUBLIC_Q, run_id=run_id, hwm=hwm, purpose="hang.x")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):  # the shielded clean-up finishes on its own
        if any(r["event"] == "model_call" for r in audit_rows(run_id)):
            break
        await asyncio.sleep(0.05)
    assert _used("finance") == used  # the reservation was refunded
    calls = [r for r in audit_rows(run_id) if r["event"] == "model_call"]
    assert [(r["decision"], r["reason"], r["route"]) for r in calls] == [("error", "cancelled", route)]
    with pgmod.connect_owner() as c:
        open_rows = c.execute("SELECT count(*) AS n FROM ops.budget_reservations WHERE run_id = %s "
                              "AND settled_at IS NULL", (run_id,)).fetchone()["n"]
    assert open_rows == 0
    egress = egress_rows(run_id)
    if route == "external":  # the payload was logged before sending: the audit row points at it
        assert len(egress) == 1 and calls[0]["details"]["egress_id"] == egress[0]["id"]
    else:
        assert egress == []


async def test_budget_day_used_by_the_gateway_is_the_utc_date(gw, staff, run_id):
    from corpai import clock

    await gw.chat(req(staff, PUBLIC_Q, run_id=run_id))
    assert ops.budget_day() == clock.now_utc().date()  # not clock.today(), the fixed sim date
    assert _used(str(staff.dept)) > 0


# ---- tracing failures after a paid call ------------------------------------------------------------

async def test_trace_end_write_failure_does_not_fail_a_billed_call(make_gw, fake, staff, run_id, audit_rows):
    """A failed trace write at span end must not turn a successful, billed call into an error
    (callers would retry: double spend, duplicate egress)."""
    from corpai.trace import Tracer

    class FailingEnd(Tracer):
        async def _finish(self, *a, **kw):
            raise RuntimeError("trace store unavailable")

    gw = make_gw()
    gw._tracer = FailingEnd()
    res = await gw.chat(req(staff, PUBLIC_Q, run_id=run_id))
    assert res.text and len(fake.calls) == 1
    assert [r["decision"] for r in audit_rows(run_id) if r["event"] == "model_call"] == ["allow"]
