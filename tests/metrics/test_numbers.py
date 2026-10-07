"""Code writes every number: render() fills placeholders, verify_numbers() catches anything else."""

from __future__ import annotations

from decimal import Decimal

import pytest

from corpai.metrics.model import NO_ACCESS, MetricValue, unavailable
from corpai.metrics.numbers import RenderError, format_value, render, table_key, verify_numbers
from corpai.types import Level


def mv(metric_id: str, value, unit: str, *, period: str = "2026-09", dims=None, suppressed=False,
       name_en: str = "", status: str = "signed") -> MetricValue:
    return MetricValue(metric_id=metric_id, period=period, dims=dims or {},
                       value=None if value is None else Decimal(str(value)), unit=unit, status=status,
                       level=Level.L3, suppressed=suppressed, name_en=name_en or metric_id, name_zh=metric_id)


TABLE = {
    "revenue_recognised": mv("revenue_recognised", "74500.00", "EUR"),
    "gross_margin_pct": mv("gross_margin_pct", "54.5450", "pct"),
    "dso_days": mv("dso_days", "85.9732", "days"),
    "customers_active": mv("customers_active", 8, "count"),
    "ar_overdue_60d": mv("ar_overdue_60d", "121500.00", "EUR", name_en="Receivables more than 60 days overdue"),
    table_key("opex_variance_pct", {"dept": "sales"}): mv("opex_variance_pct", "28.2610", "pct",
                                                          dims={"dept": "sales"}, period="2026-Q3"),
    table_key("headcount", {"dept": "legal"}): mv("headcount", None, "count", dims={"dept": "legal"},
                                                  suppressed=True),
    "cash_net_flow": unavailable("cash_net_flow", "2026-09", None, "EUR"),
}

TEMPLATE = ("Revenue was {metric:revenue_recognised} with a margin of {metric:gross_margin_pct}; DSO stands at "
            "{metric:dso_days} across {metric:customers_active} customers. Sales opex is "
            "{metric:opex_variance_pct@dept=sales:2026-Q3} over budget. Legal headcount: "
            "{metric:headcount@dept=legal}. Cash: {metric:cash_net_flow}.")


def test_render_english():
    out = render(TEMPLATE, TABLE, lang="en")
    assert "EUR 74,500" in out and "54.5%" in out and "86.0 days" in out and " 8 customers" in out
    assert "28.3% over budget" in out
    assert "[not shown: group too small]" in out
    assert f"[{NO_ACCESS}]" in out          # explicit, never a zero
    assert "{metric:" not in out


def test_render_chinese():
    out = render("收入 {metric:revenue_recognised}，DSO {metric:dso_days}，人数 {metric:headcount@dept=legal}，"
                 "现金 {metric:cash_net_flow}", TABLE, lang="zh")
    assert "74,500 欧元" in out and "86.0 天" in out and "[小样本，不展示]" in out and "[无权限或尚未发布]" in out


def test_unknown_placeholder_is_an_error():
    with pytest.raises(RenderError):
        render("Bookings {metric:bookings}", TABLE)
    with pytest.raises(RenderError):
        render("{metric:revenue_recognised:2026-08}", TABLE)  # right metric, wrong period


def test_rendered_text_verifies_clean_in_both_languages():
    for lang in ("en", "zh"):
        out = render(TEMPLATE, TABLE, lang=lang)
        assert verify_numbers(out, TABLE) == [], lang


def test_invented_number_is_caught():
    out = render(TEMPLATE, TABLE) + " Churn fell to 3.7% this quarter."
    probs = verify_numbers(out, TABLE)
    assert [p.token for p in probs] == ["3.7"]
    assert probs[0].kind == "unverified_number"


@pytest.mark.parametrize("good,bad", [("EUR 74,500", "EUR 74,600"), ("54.5%", "54.6%"), ("86.0 days", "86.9 days"),
                                      ("EUR 74,500", "EUR 74,50")])
def test_number_changed_by_one_digit_is_caught(good, bad):
    out = render(TEMPLATE, TABLE).replace(good, bad, 1)
    assert out != render(TEMPLATE, TABLE)
    probs = verify_numbers(out, TABLE)
    digits = "".join(c for c in bad if c.isdigit() or c == ".")
    assert probs, bad
    assert "".join(p.token for p in probs).replace(",", "") == digits


def test_chinese_adjacent_digits_are_scanned():
    # CJK characters count as \w; the scanner must still see digits glued to them
    probs = verify_numbers("收入为75000欧元，客户9家", TABLE)
    assert {p.token for p in probs} == {"75000", "9"}


def test_fullwidth_digits_are_scanned():
    assert [p.token for p in verify_numbers("客户共１２家", TABLE)] == ["12"]


def test_unit_must_match():
    probs = verify_numbers("We have EUR 8 in the bank.", TABLE)  # 8 is a count, not an amount
    assert [p.kind for p in probs] == ["unit_mismatch"]


def test_dates_years_ordinals_and_ids_are_allowed():
    text = ("1. On 2026-10-01 (2026年10月1日) we reviewed 2026-09 and 2026-Q3 (Q3 2026, H2).\n"
            "2) Customer A-0001, project P-02, invoice INV-00042, contract C-0003; data level L3.\n"
            "## 3. In 2025 revenue was {metric:revenue_recognised}. See [doc:fin-close-2026-09].\n")
    assert verify_numbers(render(text, TABLE), TABLE) == []


def test_a_year_used_as_an_amount_is_not_exempt():
    assert [p.token for p in verify_numbers("Marketing spent EUR 2026 on flyers.", TABLE)] == ["2026"]


def test_constants_from_metric_names_are_allowed():
    assert verify_numbers("Invoices more than 60 days overdue: {metric:ar_overdue_60d}".replace(
        "{metric:ar_overdue_60d}", format_value(TABLE["ar_overdue_60d"])), TABLE) == []


def test_leftover_placeholder_is_a_problem():
    probs = verify_numbers("Revenue {metric:revenue_recognised}", TABLE)
    assert probs and probs[0].kind == "unrendered_placeholder"


def test_sign_is_free_but_magnitude_is_not():
    table = {"opex_variance_pct@dept=eng": mv("opex_variance_pct", "-9.0999", "pct", dims={"dept": "eng"})}
    assert format_value(table["opex_variance_pct@dept=eng"]) == "-9.1%"
    assert verify_numbers("Engineering came in 9.1% under budget.", table) == []
    assert verify_numbers("Engineering came in 9.2% under budget.", table)


# ---- review findings: invented, scaled, swapped and sign-flipped figures ---------------------------

from corpai.metrics.numbers import verify_template  # noqa: E402


@pytest.mark.parametrize("text", ["Revenue reached EUR999999.", "Revenue reached EUR 3M.", "Revenue 12k.",
                                  "Margin up 15pct.", "收入约 2000 万欧元。"])
def test_glued_or_scaled_numbers_are_caught(text):
    assert verify_numbers(text, TABLE), text
    assert verify_template(text, TABLE), text


@pytest.mark.parametrize("text", ["We added 2000 new customers.", "约 2000 万欧元"])
def test_a_bare_year_shaped_count_is_a_figure(text):
    assert verify_numbers(text, TABLE) and verify_template(text, TABLE)


def test_years_in_a_date_context_stay_allowed():
    for text in ("In 2025 revenue was {metric:revenue_recognised}.", "Since 2019 we serve logistics firms.",
                 "FY2026 plan; September 2026 close; 2026年 收入 {metric:revenue_recognised}"):
        assert verify_template(text, TABLE) == [], text
        assert verify_numbers(render(text, TABLE), TABLE) == [], text


def test_a_constant_from_a_metric_name_counts_only_next_to_that_metric():
    assert verify_numbers("Opex rose 60% this quarter.", TABLE)          # ar_overdue_60d is in the table
    assert verify_template("Opex rose 60% this quarter.", TABLE)
    assert verify_template("Receivables more than 60 days overdue: {metric:ar_overdue_60d}.", TABLE) == []


def test_swapped_figures_are_caught_in_the_template():
    """Both numbers are in the table, so the rendered-text check alone cannot tell they were swapped."""
    table = {"revenue_recognised": mv("revenue_recognised", "1234567", "EUR"),
             "ar_overdue_60d": mv("ar_overdue_60d", "50000", "EUR", name_en="Receivables more than 60 days overdue")}
    swapped = "Overdue receivables reached EUR 1,234,567 while revenue was EUR 50,000."
    assert verify_numbers(swapped, table) == []          # the old, value-only check passes
    assert {p.token for p in verify_template(swapped, table)} == {"1,234,567", "50,000"}


def test_direction_word_must_agree_with_the_sign():
    table = {"opex_variance_pct@dept=sales": mv("opex_variance_pct", "23.5", "pct", dims={"dept": "sales"}),
             "opex_variance_pct@dept=eng": mv("opex_variance_pct", "-9.1", "pct", dims={"dept": "eng"})}
    bad = verify_template("Sales spent {metric:opex_variance_pct@dept=sales} below budget.", table)
    assert [p.kind for p in bad] == ["direction_mismatch"]
    assert verify_template("Sales spent {metric:opex_variance_pct@dept=sales} over budget.", table) == []
    assert verify_template("Engineering was {metric:opex_variance_pct@dept=eng} under budget.", table) == []
    assert verify_template("Engineering overspent by {metric:opex_variance_pct@dept=eng}.", table)
    assert verify_template("销售费用低于预算 {metric:opex_variance_pct@dept=sales}。", table)


def test_the_writer_falls_back_when_the_model_writes_a_figure_that_is_in_the_table():
    """A figure copied from the placeholder block onto the wrong metric renders to a table value; only
    the template check refuses it."""
    text = "Revenue was EUR 74,500 and margin {metric:gross_margin_pct}."
    assert verify_numbers(render(text, TABLE), TABLE) == []
    assert [p.token for p in verify_template(text, TABLE)] == ["74,500"]
