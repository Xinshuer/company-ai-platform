from __future__ import annotations

from datetime import date

import pytest

from corpai.metrics.periods import month_before, parse_period, quarter_before


def test_month_params():
    p = parse_period("2026-09")
    prm = p.params()
    assert (p.grain, p.start, p.end) == ("month", date(2026, 9, 1), date(2026, 10, 1))
    assert prm["periods"] == ["2026-09"] and prm["as_of"] == date(2026, 10, 1) and prm["days"] == 30
    # "next month" is the month after the reporting date's month: November for the 2026-10-01 report
    assert (prm["next_start"], prm["next_end"]) == (date(2026, 11, 1), date(2026, 12, 1))
    assert prm["ttm_start"] == date(2025, 10, 1)


def test_quarter_params():
    p = parse_period("2026-Q3")
    assert p.months == ["2026-07", "2026-08", "2026-09"]
    assert p.params()["days"] == 92 and p.as_of == date(2026, 10, 1)
    assert parse_period("2026-Q4").end == date(2027, 1, 1)


@pytest.mark.parametrize("bad", ["2026-13", "2026-9", "2026-Q5", "26-09", "2026-09; DROP TABLE x", "", None])
def test_bad_periods_are_refused(bad):
    with pytest.raises(ValueError):
        parse_period(bad)


def test_previous_complete_periods():
    assert quarter_before(date(2026, 10, 1)).text == "2026-Q3"
    assert quarter_before(date(2026, 10, 15)).text == "2026-Q3"
    assert quarter_before(date(2026, 1, 1)).text == "2025-Q4"
    assert month_before(date(2026, 10, 1)).text == "2026-09"
    assert month_before(date(2026, 1, 20)).text == "2025-12"
