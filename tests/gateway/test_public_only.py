"""Decision D5: published company facts (funding, revenue on news pages) may go external.

`public_only` relaxes only commercial-money findings, only while hwm is L1, and only because
runtime code set it; names, canaries and anything the hwm says stay as strict as before.
"""

from __future__ import annotations

import pytest

from corpai.classify import classify_text, dlp_scan
from corpai.gateway import ChatRequest
from corpai.types import Level, Route

pytestmark = [pytest.mark.anyio, pytest.mark.security]

FUNDING = ("Nordhafen Logistik GmbH raised EUR 54,000,000 in a Series C round; "
           "revenue grew to EUR 120m according to the press release.")


def _req(principal, text, *, run_id, hwm=Level.L1, public_only=False) -> ChatRequest:
    return ChatRequest(messages=[{"role": "user", "content": text}], principal=principal, hwm=hwm,
                       purpose="sales.web_extract", run_id=run_id, agent="sales_web", public_only=public_only)


def test_classify_public_caps_only_money():
    assert classify_text(FUNDING).level == Level.L3
    assert classify_text(FUNDING, public=True).level == Level.L1
    assert classify_text(FUNDING + " Contact: Anna Becker", public=True).level == Level.L3
    assert classify_text(FUNDING + " KB-CANARY-0A1B2C3D", public=True).level == Level.L4
    assert not dlp_scan(FUNDING, route=Route.EXTERNAL, public=True).blocked
    assert dlp_scan(FUNDING, route=Route.EXTERNAL).blocked


async def test_public_only_funding_page_goes_external(gw, staff, run_id):
    res = await gw.chat(_req(staff, FUNDING, run_id=run_id, public_only=True))
    assert res.route is Route.EXTERNAL and res.level == Level.L1


async def test_without_flag_the_same_text_stays_local(gw, staff, run_id):
    res = await gw.chat(_req(staff, FUNDING, run_id=run_id))
    assert res.route is Route.LOCAL and res.level == Level.L3


async def test_flag_ignored_once_hwm_is_above_l1(gw, staff, run_id):
    # e.g. CRM data already entered the session: the flag must not relax anything any more
    res = await gw.chat(_req(staff, FUNDING, run_id=run_id, hwm=Level.L2, public_only=True))
    assert res.route is Route.LOCAL and res.level == Level.L3


async def test_flag_does_not_hide_a_person_name(gw, staff, run_id):
    res = await gw.chat(_req(staff, FUNDING + " Ask Anna Becker for the deck.", run_id=run_id, public_only=True))
    assert res.route is Route.LOCAL and res.level >= Level.L3
