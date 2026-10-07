"""The management report graph (design-review 二.7, 二.8, 七 M3), run against the small company.

What these prove:
- each requester gets their own report: the CEO and the CTO see different sections and numbers;
- every number in a report is a rendered metric-table value (nothing written by the model survives);
- the cited snapshots are signed ones, with the provenance a reader follows back (SQL hash, signer);
- the planted issues reach the CEO, and every requester gets exactly the findings their own rights
  give them (the same as running the detector as that person), never more;
- groups under five people never carry an amount, and their raw figures appear nowhere;
- the writer holds no tools; a model that invents a figure is replaced by code's template.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from corpai.agents import router
from corpai.agents._rule_of_two import graph_caps, undeclared_nodes, violations
from corpai.agents.report import DOMAINS, build_graph, domain_of, export_domain, parse_request
from corpai.agents.report.compose import table_from_view
from corpai.metrics import (
    SIGNAL_KINDS,
    detect,
    format_value,
    get_def,
    load_metrics,
    render,
    verify_numbers,
    verify_template,
)
from corpai.metrics.numbers import PLACEHOLDER_RE
from tests.m3.conftest import REPORT_PERIOD, REPORT_PERSONAS, owner_rows, run_report

PLANTED_KINDS = ("overdue_receivable", "contract_expiring", "project_testing_delayed", "hiring_over_budget",
                 "dept_budget_overrun", "missing_dpa_clause")
SMALL_DEPTS = ("exec", "it", "legal")  # 2, 3 and 4 people (docs/company.md)


def _data(rep: dict) -> dict:
    return rep["output"]["data"]


def _sections(rep: dict) -> dict[str, dict]:
    return {s["domain"]: s for s in _data(rep)["sections"]}


def _shown(rep: dict) -> dict[str, str]:
    """placeholder key -> displayed value, for the metric rows the requester can see."""
    return {k: v["display_zh"] for k, v in _data(rep)["table"].items() if v["status"] != "unavailable"}


# ---- structure ---------------------------------------------------------------------------------------

def test_every_metric_and_signal_has_a_section():
    assert {domain_of(m) for m in load_metrics().values()} <= {d.id for d in DOMAINS}
    assert sorted(k for d in DOMAINS for k in d.signals) == sorted(SIGNAL_KINDS)


def test_graph_keeps_rule_of_two_and_holds_sensitive_data_only():
    g = router.buildable_apps()["report"]()
    assert undeclared_nodes(g) == [] and violations(g) == []
    caps = graph_caps(g)
    assert set(caps) >= {"plan", "write", "finish"} | {f"fetch_{d.id}" for d in DOMAINS}
    # no node reads untrusted content or acts outside: the writer has no tools, nothing writes
    assert all(c <= {"sensitive_data"} and not gate for c, gate in caps.values())


def test_the_report_agent_holds_no_write_or_web_tool():
    from corpai.policy import agent_spec, tool_tier
    from corpai.types import ToolTier

    spec = agent_spec("report")
    for q in spec.tools:
        domain, _, tool = q.partition(".")
        assert domain != "web" and tool_tier(domain, tool) is ToolTier.READ, q


@pytest.mark.parametrize("text,period,lang", [
    ("2026-Q2 管理层报告", "2026-Q2", "zh"), ("report for September 2026", "2026-09", "en"),
    ("2026 年 9 月管理摘要", "2026-09", "zh"), ("2026年第二季度", "2026-Q2", "zh"), ("management report", "2026-Q3", "en"),
])
def test_period_and_language_come_from_code(text, period, lang):
    from datetime import date

    req = parse_request(text, date(2026, 10, 1))
    assert (req.period, req.lang) == (period, lang)


@pytest.mark.anyio
async def test_a_period_that_has_not_ended_is_refused(q2, personas):
    rep = await run_report(personas["ceo"], "2026-10 report")
    assert _data(rep)["refused"] is True and "not ended" in rep["output"]["text"]
    assert not rep.get("sections")


# ---- different requesters, different reports ------------------------------------------------------

def test_ceo_and_cto_reports_differ_in_sections_and_numbers(reports):
    ceo, cto = reports["ceo"], reports["eng_head"]
    cs, ts = _sections(ceo), _sections(cto)
    assert {d: s["status"] for d, s in cs.items()} == {d.id: "ok" for d in DOMAINS}
    assert ts["legal"]["status"] == "no_permission" and ts["finance"]["status"] == "partial"
    # signed but not published: the CEO reads it, the CTO gets an explicit "no permission or not published"
    rev_c, rev_t = _data(ceo)["table"]["revenue_recognised"], _data(cto)["table"]["revenue_recognised"]
    assert rev_c["status"] == "signed" and rev_c["value"] is not None
    assert rev_t["status"] == "unavailable" and rev_t["value"] is None and rev_t["display_zh"] == "[无权限或尚未发布]"
    # per-dept rows: the CTO sees engineering's own, the CEO every dept big enough to show
    assert {k for k in _shown(cto) if k.startswith("opex_actual@")} == {"opex_actual@dept=eng"}
    assert {k for k in _shown(ceo) if k.startswith("opex_actual@")} >= {"opex_actual@dept=eng", "opex_actual@dept=sales",
                                                                       "opex_actual@dept=finance"}
    metric_keys = lambda rep: {k for k in _shown(rep) if not k.startswith("signal.")}  # noqa: E731
    assert metric_keys(ceo) > metric_keys(cto)
    assert ceo["output"]["text"] != cto["output"]["text"]
    # the CTO's report carries a figure the CEO's cannot: the red CI streak behind P-02
    streak = "signal.project_testing_delayed.P-02.trailing_failed_runs"
    assert streak in _shown(cto) and streak not in _data(ceo)["table"]
    # the same evidence differs too: the CTO sees CI and tickets behind P-02, the CEO the stage only
    p02_c = cs["eng"]["signals"][0]["findings"][0]
    p02_t = ts["eng"]["signals"][0]["findings"][0]
    assert p02_c["ci_visible"] is False and p02_t["ci_visible"] is True and p02_t["inconsistent"] is True
    # an explicit denial in the CTO's text: the no-permission section is named, not skipped
    assert "法务：整个部分无权限查看或尚未发布" in cto["output"]["text"]


def test_every_requester_gets_their_own_report(reports):
    texts = {k: r["output"]["text"] for k, r in reports.items()}
    assert len(set(texts.values())) == len(REPORT_PERSONAS)
    for key, rep in reports.items():
        assert _data(rep)["export"]["domain"] == ("company" if key == "ceo" else key.split("_")[0])


# ---- every number traces to a metric value ----------------------------------------------------------

def test_every_number_in_the_report_is_a_rendered_table_value(reports):
    for key, rep in reports.items():
        full = table_from_view(_data(rep)["table"])
        template, text = _data(rep)["template"], rep["output"]["text"]
        assert render(template, full, lang="zh") == text, key
        assert verify_template(template, full) == [], key
        assert verify_numbers(text, full) == [], key
        # every placeholder names a table entry; every metric row of every section is in the table
        keys = {m.group(1) for m in PLACEHOLDER_RE.finditer(template)}
        assert keys <= set(full), key
        assert {m["key"] for s in _data(rep)["sections"] for m in s["metrics"]} <= set(full), key
        for k, mv in full.items():
            assert _data(rep)["table"][k]["display_zh"] == format_value(mv, "zh")
        # every issue the requester may see is named by its ref (a text that drops one is replaced)
        for sec in _data(rep)["sections"]:
            for sig in sec["signals"]:
                for f in sig["findings"] if sig["status"] == "ok" else ():
                    assert re.search(rf"(?<![\w-]){re.escape(f['ref'])}(?![\w-])", text), (key, f["ref"])


def test_cited_snapshots_are_signed_and_carry_their_provenance(reports, personas):
    cfo = personas["finance_head"].user_id
    for key, rep in reports.items():
        for k, v in _data(rep)["table"].items():
            if k.startswith("signal."):
                assert v["status"] == "computed" and v["provenance"]["sources"], (key, k)
                continue
            if v["status"] == "unavailable":
                continue
            assert v["status"] in ("signed", "published"), (key, k)  # never a draft (design-review 二.8)
            prov = v["provenance"]
            assert prov["sql_sha256"] == get_def(v["metric_id"]).sql_sha256, (key, k)
            assert prov["signed_by"] == cfo and prov["formula"] and prov["sources"], (key, k)
            assert "sql" not in prov  # the hash, never the SQL text


@pytest.mark.anyio
async def test_drafts_are_never_cited(seeded, personas):
    # 2026-Q3 holds drafts only (tests/metrics): the CEO, who may read drafts, still gets no figure
    rep = await run_report(personas["ceo"], "2026-Q3 管理层报告")
    metrics = [v for k, v in _data(rep)["table"].items() if not k.startswith("signal.")]
    assert metrics and all(v["status"] == "unavailable" and v["value"] is None for v in metrics)
    assert "[无权限或尚未发布]" in rep["output"]["text"]


# ---- planted issues, as each requester may see them -------------------------------------------------

# Signals keyed to the sim date (an overdue invoice, a contract expiring next month) show today's state in
# any report; the dept overrun compares figures over a period and runs on the report's own period.
PERIOD_SIGNALS = {"dept_budget_overrun"}


def _opts(kind: str, period: str) -> dict:
    return {"period": period} if kind in PERIOD_SIGNALS else {}


def test_planted_issues_appear_in_the_ceo_report(reports, planted):
    ceo = reports["ceo"]
    found = {s["kind"]: [f["ref"] for f in s["findings"]] for sec in _data(ceo)["sections"] for s in sec["signals"]}
    text = ceo["output"]["text"]
    for kind in PLANTED_KINDS:
        if kind in PERIOD_SIGNALS:
            continue  # planted in 2026-Q3: the Q3 report test below
        ref = planted[kind]["ref"]
        assert ref in found[kind], kind
        assert ref in text, kind


def test_a_q2_report_does_not_show_the_q3_overrun_as_its_own(reports):
    """Sales was under budget in 2026-Q2 (its opex variance in the same section is negative): the Q2
    report must not list the planted 2026-Q3 overrun among its issues."""
    ceo = reports["ceo"]
    variance = _data(ceo)["table"]["opex_variance_pct@dept=sales"]
    assert variance["value"] is not None and Decimal(str(variance["value"])) < 0
    over = next(s for s in _sections(ceo)["finance"]["signals"] if s["kind"] == "dept_budget_overrun")
    assert over["status"] == "ok" and "sales" not in [f["ref"] for f in over["findings"]]
    assert all(f["period"] == REPORT_PERIOD for f in over["findings"])
    assert not any(k.startswith("signal.dept_budget_overrun.sales.") for k in _data(ceo)["table"])


@pytest.mark.anyio
async def test_the_q3_report_shows_the_planted_overrun_with_its_period(seeded, personas, planted):
    rep = await run_report(personas["ceo"], "2026-Q3 管理层报告")
    text = rep["output"]["text"]
    over = next(s for s in _sections(rep)["finance"]["signals"] if s["kind"] == "dept_budget_overrun")
    f = next(f for f in over["findings"] if f["ref"] == "sales")
    assert f["period"] == "2026-Q3"
    assert f["overrun_pct"] == pytest.approx(planted["dept_budget_overrun"]["overrun_pct"], abs=0.01)
    # rendered by code from the detector's evidence, and the issue line says which period it is about
    shown = _data(rep)["table"]["signal.dept_budget_overrun.sales.overrun_pct"]["display_zh"]
    assert shown in text and shown == f"{Decimal(str(f['overrun_pct'])).quantize(Decimal('0.1'))}%"
    line = next(x for x in text.splitlines() if x.startswith("- issue dept_budget_overrun: ref sales"))
    assert "2026-Q3" in line


@pytest.mark.anyio
async def test_a_month_report_checks_the_overrun_of_that_month(seeded, personas):
    rep = await run_report(personas["finance_head"], "2026-09 management report")
    over = next(s for s in _sections(rep)["finance"]["signals"] if s["kind"] == "dept_budget_overrun")
    own = await detect(personas["finance_head"], "dept_budget_overrun", period="2026-09")
    assert over["findings"] == [dict(x) for x in json.loads(json.dumps(list(own.findings)))]
    assert all(f["period"] == "2026-09" and f["periods"] == ["2026-09"] for f in over["findings"])


@pytest.mark.anyio
@pytest.mark.parametrize("who", REPORT_PERSONAS)
async def test_each_requester_gets_exactly_the_findings_their_rights_give(reports, personas, planted, who):
    rep = reports[who]
    got = {s["kind"]: s for sec in _data(rep)["sections"] for s in sec["signals"]}
    text, blob = rep["output"]["text"], json.dumps(_data(rep), ensure_ascii=False)
    visible_refs = set()
    for kind in SIGNAL_KINDS:
        own = await detect(personas[who], kind, **_opts(kind, REPORT_PERIOD))
        assert got[kind]["status"] == own.status, (who, kind)
        assert [f["ref"] for f in got[kind]["findings"]] == own.refs, (who, kind)
        visible_refs |= set(own.refs)
    ceo = {s["kind"]: s for sec in _data(reports["ceo"])["sections"] for s in sec["signals"]}
    metric_dims = {v for m in _data(rep)["table"].values() for v in (m.get("dims") or {}).values()}
    for kind in PLANTED_KINDS:
        if got[kind]["status"] == "ok":
            continue
        # a finding the requester may not see appears nowhere: no evidence, no figure, no issue line
        assert not any(k.startswith(f"signal.{kind}.") for k in _data(rep)["table"]), (who, kind)
        for f in ceo[kind]["findings"]:
            evidence = [f.get(x) for x in ("account_name", "counterparty", "notice_deadline", "stage_since")]
            evidence += list(f.get("invoices_over_threshold") or []) + list(f.get("claim_ticket_ids") or [])
            for item in filter(None, evidence):
                assert str(item) not in blob and str(item) not in text, (who, kind, item)
            ref = f["ref"]
            if ref.startswith(("A-", "C-", "R-")) and ref not in visible_refs | metric_dims:
                assert not re.search(rf"\b{re.escape(ref)}\b", text) and ref not in blob, (who, kind, ref)
        if kind not in PERIOD_SIGNALS:
            assert planted[kind]["ref"] in [f["ref"] for f in ceo[kind]["findings"]]


# ---- groups under five people -----------------------------------------------------------------------

def _raw_q2_opex(dept: str) -> Decimal:
    rows = owner_rows(
        "SELECT coalesce(sum(j.debit_eur - j.credit_eur), 0) AS v FROM fin.journal_entries j "
        "JOIN fin.chart_of_accounts a ON a.account_code = j.account_code "
        "WHERE a.type = 'expense' AND j.status = 'posted' AND j.dept = %s AND j.period = ANY (%s)",
        (dept, ["2026-04", "2026-05", "2026-06"]))
    return Decimal(rows[0]["v"])


def test_small_groups_never_carry_an_amount(reports):
    for key, rep in reports.items():
        for k, v in _data(rep)["table"].items():
            if v.get("suppressed"):
                assert v["value"] is None and v["display_zh"] == "[小样本，不展示]", (key, k)
            if "@dept=" in k and k.split("@dept=")[1] in SMALL_DEPTS and get_def(v["metric_id"]).min_group > 1:
                assert v["value"] is None, (key, k)
        for sec in _data(rep)["sections"]:
            for s in sec["signals"]:
                for f in s["findings"]:
                    if s["kind"] == "dept_budget_overrun" and f["dept"] in SMALL_DEPTS:
                        assert f.get("suppressed") and "actual_eur" not in f and "overrun_pct" not in f


def test_raw_figures_of_small_departments_appear_nowhere(reports):
    for dept in SMALL_DEPTS:
        raw = _raw_q2_opex(dept)
        assert raw > 0, dept
        forms = {f"{int(raw.quantize(Decimal(1))):,}", str(int(raw.quantize(Decimal(1)))), str(raw)}
        for key, rep in reports.items():
            blob = rep["output"]["text"] + json.dumps(_data(rep), ensure_ascii=False)
            assert not any(f in blob for f in forms), (key, dept)


# ---- level, route, events ------------------------------------------------------------------------

def test_level_route_and_explicit_denials_in_the_timeline(reports):
    for key, rep in reports.items():
        data, events = _data(rep), rep["events"]
        assert data["level"] == rep["sensitivity_hwm"] >= 3, key  # confidential rows: written locally
        assert data["route"] == "local" and any(e["t"] == "route" and e["route"] == "local" for e in events), key
        hw = [e for e in events if e["t"] == "hwm"]
        assert len(hw) == 1 and hw[0]["to"] == data["level"], key  # one rise, after the fan-out
        assert sum(e["t"] == "notice" and e.get("code") == "local_mode" for e in events) == 1, key
        tools = [e for e in events if e["t"] == "tool" and e["domain"] == "metrics"]
        assert sorted(e["tool"] for e in tools) == sorted(d.id for d in DOMAINS), key
        for e in tools:
            sec = _sections(rep)[e["tool"]]
            assert (e["decision"] == "deny") == (sec["status"] == "no_permission"), key
            hidden = [m for m in sec["metrics"] if m["status"] == "unavailable"]
            assert len([d for d in e["denied"] if d["id"].startswith("metric:")]) == min(len(hidden), 20), key


@pytest.mark.anyio
async def test_user_level_raises_the_report_level(q2, personas):
    rep = await run_report(personas["sales_head"], user_level=4)
    assert rep["sensitivity_hwm"] == 4 and _data(rep)["level"] == 4


def test_export_domain_comes_from_the_requester(personas):
    assert export_domain(personas["ceo"]) == "company"
    assert export_domain(personas["eng_head"]) == "eng"


# ---- the writer -------------------------------------------------------------------------------------

@pytest.fixture
def lying_writer():
    from corpai.gateway import fake_llm
    from corpai.metrics.writer import PURPOSE, register_fake

    def lie(messages, *, route, json_mode):
        return "Revenue rose to EUR 9,999,999 and headcount is 4321. {metric:arr}\n"

    fake_llm().register(PURPOSE, lie)
    yield
    register_fake()


@pytest.mark.anyio
async def test_a_writer_that_invents_figures_is_replaced_by_code(q2, personas, lying_writer):
    rep = await run_report(personas["ceo"])
    data, text = _data(rep), rep["output"]["text"]
    assert data["used_fallback"] is True and "number_outside_placeholder" in data["problems"]
    assert "9,999,999" not in text and "4321" not in text
    assert verify_numbers(text, table_from_view(data["table"])) == []


@pytest.mark.anyio
async def test_the_writer_runs_with_the_report_level_and_no_tools(q2, personas, fake_llm):
    rep = await run_report(personas["eng_head"])
    calls = [c for c in fake_llm.calls if c["purpose"] == "report.write"]
    assert len(calls) == 1, "one writer call through the gateway"
    assert not calls[0]["tools"]  # the writer holds no tools
    assert calls[0]["route"] == "local" and calls[0]["level"] == _data(rep)["level"]
    # the model sees placeholders and displayed values only: no SQL, no tokens
    prompt = json.dumps(calls[0]["messages"], ensure_ascii=False)
    assert "{metric:" in prompt and "SELECT" not in prompt and "eyJ" not in prompt


def test_graph_compiles_without_a_checkpointer():
    build_graph().compile()
