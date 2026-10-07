"""Rule 6 (per-route concurrency with a queue timeout) and rule 8 (timeouts, retries with backoff)."""

from __future__ import annotations

import asyncio

import pytest

from corpai.gateway import BackendError, ChatRequest, GatewayBusy, RetryableBackendError, RetryConfig
from corpai.types import Level, Route

pytestmark = pytest.mark.anyio

FAST_RETRY = RetryConfig(max_attempts=3, base_delay_s=0.01, max_delay_s=0.02)


def req(principal, *, run_id, purpose="slow.answer", hwm=Level.L1) -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": "What is deferred revenue?"}], principal=principal,
                       hwm=hwm, purpose=purpose, run_id=run_id, agent="qa")


async def _slow(messages, *, route, json_mode):
    await asyncio.sleep(1.0)
    return "slow answer"


async def test_queue_timeout_gives_gateway_busy(make_gw, fake, staff, run_id, audit_rows):
    gw = make_gw(external={"max_concurrency": 1, "queue_timeout_s": 0.2})
    fake.register("slow.", _slow)
    results = await asyncio.gather(gw.chat(req(staff, run_id=run_id)), gw.chat(req(staff, run_id=run_id)),
                                   return_exceptions=True)
    ok = [r for r in results if not isinstance(r, BaseException)]
    busy = [r for r in results if isinstance(r, GatewayBusy)]
    assert len(ok) == 1 and len(busy) == 1, results
    assert len(fake.calls) == 1
    reasons = sorted(r["reason"] for r in audit_rows(run_id))
    assert reasons == ["gateway_busy", "route_by_level"]


async def test_routes_have_separate_slots(make_gw, fake, staff, run_id):
    """A full external route does not block local calls (and vice versa)."""
    gw = make_gw(external={"max_concurrency": 1, "queue_timeout_s": 0.2},
                 local={"max_concurrency": 1, "queue_timeout_s": 0.2})
    fake.register("slow.", _slow)
    results = await asyncio.gather(gw.chat(req(staff, run_id=run_id)),
                                   gw.chat(req(staff, run_id=run_id, hwm=Level.L3)))
    assert {r.route for r in results} == {Route.EXTERNAL, Route.LOCAL}


async def test_slot_released_after_errors(make_gw, fake, staff, run_id):
    gw = make_gw(external={"max_concurrency": 1, "queue_timeout_s": 0.5}, retry=FAST_RETRY)

    def boom(messages, *, route, json_mode):
        raise RuntimeError("x")

    fake.register("boom.", boom)
    for _ in range(3):
        with pytest.raises(BackendError):
            await gw.chat(req(staff, run_id=run_id, purpose="boom.x"))
    assert (await gw.chat(req(staff, run_id=run_id, purpose="ok.x"))).text


async def test_retry_on_429_and_5xx_then_success(make_gw, fake, staff, run_id, audit_rows):
    gw = make_gw(retry=FAST_RETRY)
    statuses = [429, 503]

    def flaky(messages, *, route, json_mode):
        if statuses:
            raise RetryableBackendError("transient", status=statuses.pop(0))
        return "finally"

    fake.register("flaky.", flaky)
    res = await gw.chat(req(staff, run_id=run_id, purpose="flaky.x"))
    assert res.text == "finally" and len(fake.calls) == 3
    (row,) = audit_rows(run_id)
    assert row["details"]["attempts"] == 3


async def test_retries_are_bounded(make_gw, fake, staff, run_id, audit_rows):
    gw = make_gw(retry=FAST_RETRY)
    fake.register("down.", lambda messages, *, route, json_mode: (_ for _ in ()).throw(
        RetryableBackendError("HTTP 500", status=500)))
    with pytest.raises(BackendError) as ei:
        await gw.chat(req(staff, run_id=run_id, purpose="down.x"))
    assert ei.value.status == 500 and "after 3 attempts" in str(ei.value)
    assert len(fake.calls) == 3
    (row,) = audit_rows(run_id)
    assert (row["event"], row["decision"], row["reason"]) == ("model_call", "error", "backend_error")


async def test_backoff_is_exponential():
    r = RetryConfig(max_attempts=5, base_delay_s=0.5, max_delay_s=3)
    assert [r.delay(a) for a in (1, 2, 3, 4)] == [0.5, 1.0, 2.0, 3]


async def test_per_attempt_timeout(make_gw, fake, staff, run_id):
    gw = make_gw(external={"timeout_s": 0.05}, retry=RetryConfig(max_attempts=2, base_delay_s=0.01))
    fake.register("slow.", _slow)
    with pytest.raises(BackendError) as ei:
        await gw.chat(req(staff, run_id=run_id))
    assert len(fake.calls) == 2 and ei.value.retryable


async def test_backend_bug_message_is_not_leaked(make_gw, fake, staff, run_id):
    """An arbitrary backend exception may quote the payload; only its class name survives."""
    gw = make_gw(retry=FAST_RETRY)

    def leaky(messages, *, route, json_mode):
        raise RuntimeError("could not process: Erika Example salary 98000")

    fake.register("leaky.", leaky)
    with pytest.raises(BackendError) as ei:
        await gw.chat(req(staff, run_id=run_id, purpose="leaky.x"))
    assert "Erika" not in str(ei.value) and "RuntimeError" in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__context__ is None
    assert len(fake.calls) == 1  # not retried
