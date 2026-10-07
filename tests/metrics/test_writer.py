"""The report writer: model prose with placeholders, code-rendered numbers, verified; never a wrong figure."""

from __future__ import annotations

import pytest

from corpai.metrics import detect_all, get_metric, table_key, verify_numbers
from corpai.metrics import writer
from corpai.types import Level

pytestmark = pytest.mark.anyio


async def _inputs(personas, who: str = "ceo"):
    p = personas[who]
    table = {
        "revenue_recognised": await get_metric(p, "revenue_recognised", "2026-09"),
        "gross_margin_pct": await get_metric(p, "gross_margin_pct", "2026-09"),
        "ar_overdue_60d": await get_metric(p, "ar_overdue_60d", "2026-09"),
        table_key("opex_variance_pct", {"dept": "sales"}): await get_metric(p, "opex_variance_pct", "2026-Q3",
                                                                            {"dept": "sales"}),
        table_key("headcount", {"dept": "legal"}): await get_metric(p, "headcount", "2026-09", {"dept": "legal"}),
    }
    return p, table, await detect_all(p)


async def test_fake_writer_output_is_verified_and_routed_local(drafts, personas, fake_llm):
    p, table, signals = await _inputs(personas)
    draft = await writer.write_report_text(p, run_id="run_w1", table=table, signals=signals, hwm=Level.L1, lang="en")
    assert not draft.used_fallback and draft.attempts == 1
    assert verify_numbers(draft.text, {**table, **writer.signal_table(signals)}) == []
    assert "{metric:" not in draft.text
    assert "EUR 121,500" in draft.text                 # A-0001's > 60 days amount, rendered by code
    assert "[not shown: group too small]" in draft.text  # legal headcount, suppressed
    # confidential figures went to the model: the code-computed hwm forces the local route
    assert draft.level >= Level.L3 and draft.route == "local"
    call = fake_llm.calls[-1]
    assert call["purpose"] == "report.write" and call["route"] == "local" and call["tools"] is None


async def test_invented_number_falls_back_to_code_template(drafts, personas, fake_llm):
    p, table, signals = await _inputs(personas)
    fake_llm.register("report.write", lambda messages, **kw: "Revenue grew to EUR 99,999 and margin hit 71.2%.")
    try:
        draft = await writer.write_report_text(p, run_id="run_w2", table=table, signals=signals, lang="zh")
    finally:
        writer.register_fake()
    assert draft.used_fallback and draft.attempts == 2
    assert {pr.token for pr in draft.problems} == {"99,999", "71.2"}
    assert "99,999" not in draft.text and "71.2" not in draft.text
    assert verify_numbers(draft.text, {**table, **writer.signal_table(signals)}) == []
    # the retry told the model what was wrong without echoing any figure from the table
    retry = [c for c in fake_llm.calls if c["purpose"] == "report.write"][-1]
    assert "rejected" in retry["messages"][-1]["content"]


async def test_unknown_placeholder_falls_back(drafts, personas, fake_llm):
    p, table, signals = await _inputs(personas)
    fake_llm.register("report.write", lambda messages, **kw: "Bookings: {metric:bookings_invented}")
    try:
        draft = await writer.write_report_text(p, run_id="run_w3", table=table, signals=signals)
    finally:
        writer.register_fake()
    assert draft.used_fallback and draft.problems[0].kind == "unknown_metric"


async def test_figures_the_reader_cannot_see_stay_hidden(drafts, personas, fake_llm):
    p, table, signals = await _inputs(personas, "sales_staff")  # drafts only for 2026-09: nothing visible
    assert all(not mv.available for mv in table.values())
    draft = await writer.write_report_text(p, run_id="run_w4", table=table, signals=signals, lang="en")
    assert "[no permission or not published]" in draft.text
    assert "no permission" in draft.text.lower()
    sent = "\n".join(m["content"] for m in fake_llm.calls[-1]["messages"])
    assert "121,500" not in sent   # the model never saw a figure the reader may not see
