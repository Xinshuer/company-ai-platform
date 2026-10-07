"""Routing samples: every row of design-review 四.3 holds (evals/routing/routing_samples.yaml).

Rows run through the real ModelGateway (FakeLLM backend) as the persona, with the hwm the runtime
would set from the attached sources and playbooks; the gateway alone picks the route. The rows
that are about a conversation run through the Q&A app end to end:

- "前两轮是公开问题，第 3 步拉入 CRM 数据": external, external, then local from the step that
  pulled CRM data on, and only a new thread resets it;
- "标签缺失、分类器超时、local 宕机": unknown source, classifier failure, no gazetteer and the
  local outage all end on the local route or in an error, never external;
- and rule 3 of 四.2 ("员工可以调高"): the user's raised level is a floor in every ready app.
"""

from __future__ import annotations

import pytest

from corpai.classify import load_gazetteer_from_db, source_default
from corpai.gateway import FakeLLM, LocalUnavailable, ModelGateway, load_config
from corpai.types import Level, Route
from evals.routing.samples import SAMPLES, ask, fill, holds, sample_hwm
from tests.api.support import set_switch_owner
from tests.security import _apps

pytestmark = [pytest.mark.anyio, pytest.mark.security]

PUBLIC_Q = "What does FleetPilot do?"
GLOSSARY_Q = "What is deferred revenue?"
CRM_Q = "Which customer accounts need attention?"


@pytest.fixture
def gateway(seeded) -> ModelGateway:
    load_gazetteer_from_db()
    return ModelGateway(FakeLLM(), config=load_config())


def test_every_row_of_the_table_has_a_sample():
    rows = {s["row"].split(" (")[0].split(" | ")[1] for s in SAMPLES}
    table = {"只用公司名查官网、融资、新闻", "给某位具体联系人建画像、判断意向", "结合历史报价和折扣给跟进策略",
             "通用编程问题，不贴公司代码", "解释或重构公司代码", "带用户手机号的日志", "分析安全漏洞",
             "查 GDPR 条文和监管指南", "问“符合 GDPR 吗”，同时粘贴客户合同条款", "按 Playbook 初审合同",
             "整理诉讼材料", "“什么是递延收入”", "汇总本月费用明细", "生成季度财务报表", "起草公开 JD（不含薪资预算）",
             "汇总编制需求", "初筛简历", "初筛简历（筛选结论 L4）", "整理薪资和绩效", "行业趋势研究（不带内部数据）",
             "各部门人数、项目进度汇总（去掉个人信息）", "含财务和薪资的经营报告", "标签缺失"}
    missing = {r for r in table if not any(r in s["row"] for s in SAMPLES)}
    assert not missing, missing
    assert rows <= table | {"按 Playbook 初审合同 (Playbook 本身就是 L3)"}
    assert len({s["id"] for s in SAMPLES}) == len(SAMPLES)


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
async def test_routing_sample_holds(sample, gateway, personas):
    p = personas[sample["persona"]]
    res = await ask(gateway, p, fill(sample["text"]), sample_hwm(sample))
    ok = holds(sample, res.route, res.level)
    if sample.get("known_gap") and not ok:
        pytest.xfail(sample["known_gap"])
    assert ok, f"{sample['row']}: got {res.route} at L{int(res.level)}"


async def test_the_session_stays_local_from_the_step_that_pulled_crm_data(app_client, personas):
    """任意 | 前两轮是公开问题，第 3 步拉入 CRM 数据 | L1→L3 | 从第 3 步起全部 local，开新会话才重置"""
    await _apps.as_persona(app_client, personas, "sales_staff")
    one = await _apps.post_run(app_client, "qa", PUBLIC_Q)
    two = await _apps.post_run(app_client, "qa", GLOSSARY_Q, thread_id=one["thread_id"])
    three = await _apps.post_run(app_client, "qa", CRM_Q, thread_id=one["thread_id"])
    four = await _apps.post_run(app_client, "qa", PUBLIC_Q, thread_id=one["thread_id"])
    fresh = await _apps.post_run(app_client, "qa", PUBLIC_Q)
    assert [r["status"] for r in (one, two, three, four, fresh)] == ["done"] * 5
    assert [r["route"] for r in (one, two, three, four, fresh)] == ["external", "external", "local", "local",
                                                                    "external"]
    assert one["hwm"] <= 2 and two["hwm"] <= 2 and three["hwm"] >= 3 and four["hwm"] >= three["hwm"]
    assert fresh["thread_id"] != one["thread_id"] and fresh["hwm"] <= 2
    assert any(e["t"] == "tool" and e["domain"] == "crm" for e in three["events"])
    # one route badge per run (QA polish): the answer call's route, which is the run's route
    for r in (one, two, three, four, fresh):
        badges = [e for e in r["events"] if e["t"] == "route"]
        assert len(badges) == 1 and badges[0]["route"] == r["route"]


async def test_unknown_source_is_l4_and_local(gateway, personas):
    """任意 | 标签缺失 → treated as the highest level (rule 4: unsure means higher)."""
    assert source_default("ops.unlisted_system") == Level.L4 and source_default("") == Level.L4
    res = await ask(gateway, personas["sales_staff"], GLOSSARY_Q, source_default("no.such.system"))
    assert (res.route, res.level) == (Route.LOCAL, Level.L4)


async def test_classifier_failure_is_l4_and_local(gateway, personas, monkeypatch):
    """任意 | 分类器超时: any detector failure classifies as L4, so the call stays local."""
    from corpai.classify import detectors

    def broken(text):
        raise TimeoutError("detector timed out")

    monkeypatch.setattr(detectors, "detect", broken)
    res = await ask(gateway, personas["eng_staff"], GLOSSARY_Q)
    assert (res.route, res.level) == (Route.LOCAL, Level.L4)


async def test_no_gazetteer_means_local(gateway, personas, monkeypatch):
    """Without the person-name list a name cannot be recognised: unsure means local."""
    from corpai.classify import names, set_gazetteer

    monkeypatch.setattr(names, "_state", names._state)  # put the session's gazetteer back afterwards
    set_gazetteer([])
    res = await ask(gateway, personas["eng_staff"], GLOSSARY_Q)
    assert res.route == Route.LOCAL and res.level >= Level.L3


async def test_local_down_never_goes_external(gateway, personas):
    """任意 | local 宕机: L3/L4 requests error; nothing is sent anywhere."""
    before = _apps.max_id("ops.egress_log", "id")
    set_switch_owner("local_down", True)
    try:
        for hwm in (Level.L3, Level.L4):
            with pytest.raises(LocalUnavailable):
                await ask(gateway, personas["sales_staff"], GLOSSARY_Q, hwm)
    finally:
        set_switch_owner("local_down", False)
    assert _apps.egress_after(before) == []


# app -> known gap tolerated as an expected failure. Empty since the wiring integration made every
# graph apply the user's raised level (architecture §12: hwm = max(classify_text(input),
# ctx.user_level)); see issues-log WI.
USER_LEVEL_GAPS: dict[str, str] = {}
PERSONAS_PER_APP = 4


@pytest.mark.parametrize("app", _apps.all_apps())
async def test_the_users_raised_level_is_a_floor(app, app_client, personas, fake_llm):
    """四.2 rule 3: an employee may raise the level. At user level 3 nothing of the run goes external,
    every model call carries at least L3, and the session records at least L3."""
    if not _apps.router.app_ready(app):
        pytest.skip(f"{app}: graph not built yet (router.app_ready is False)")
    before = _apps.max_id("ops.egress_log", "id")
    problems: list[str] = []
    for key in _apps.spread(_apps.starters(app, personas), PERSONAS_PER_APP):
        await _apps.as_persona(app_client, personas, key)
        run = await _apps.post_run(app_client, app, _apps.inputs_for(app)[0], user_level=int(Level.L3))
        assert run["http"] == 200, (key, run)
        audit = _apps.audit_for_runs([run["run_id"]])
        if [a for a in audit if a["event"] == "model_call" and (a["details"] or {}).get("hwm", 0) < Level.L3]:
            problems.append(f"{key}: model call below the user's level")
        if (run.get("hwm") or 0) < Level.L3:
            problems.append(f"{key}: session hwm {run.get('hwm')}")
    if _apps.egress_after(before):
        problems.append("egress")
    if [c for c in fake_llm.calls if c["route"] == "external"]:
        problems.append("external model call")
    if problems and app in USER_LEVEL_GAPS:
        pytest.xfail(f"{app}: {USER_LEVEL_GAPS[app]} ({'; '.join(problems)})")
    assert problems == [], (app, problems)
