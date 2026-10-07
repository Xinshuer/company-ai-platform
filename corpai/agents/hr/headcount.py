"""hr_headcount graph (M4; design-review 三 HR 编制需求按字段拆开, decision D7; issues-log D-10).

    summarize   corpai.hr.headcount_summary as the user: the public part (L2: roles, open headcount,
                staff per department with n<5 hidden in SQL) and the budget part (L3, only rows the
                user may read). hwm = the summary's level: L3 as soon as one budget row is included.
    note        a short model note over the PUBLIC part only. It runs at the session's hwm, so it goes
                external for someone who sees no budget (L2) and local for anyone who does (L3). The
                note may not contain digits: every number on the page comes from the code-built table.

No write, no web, no untrusted input; no tool calls (the summary reads through RLS directly).
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, START, StateGraph

from corpai.agents.hr import _fake
from corpai.agents.runtime import BaseState, RunContext, Step, node
from corpai.hr import headcount_summary
from corpai.types import Level

APP = "hr_headcount"
_DIGIT = re.compile(r"\d")
NOTE_SYSTEM = ("You summarise Kestrelbyte's open hiring for an internal reader in two sentences of plain prose. "
               "Use only the JSON given. Do not write any numbers or digits: the figures are shown in a table "
               "next to your text. Say 'hidden' for suppressed groups and 'no permission' for denied ones.")
CODE_NOTE = "Open roles and headcount per department are in the table; small groups are hidden."


class HeadcountState(BaseState, total=False):
    question: str
    summary: dict | None
    output: dict | None


def initial_state(text: str) -> dict:
    return {"question": text, "summary": None, "output": None}


@node(caps={"sensitive_data"})
async def summarize(state: HeadcountState, runtime: Any) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    # NoPermission (a PermissionError) propagates: the run answers 403-style "forbidden"
    summary = await headcount_summary(ctx.principal, run_id=ctx.run_id)
    step.raise_to(int(summary["level"]), "hr:headcount_budget" if summary["budget"]["allowed"] else "hr:headcount")
    # The user's raised level is a floor (design-review 四.2 rule 3): at L3 the note goes local too.
    user_level = Level(max(1, min(4, int(ctx.user_level))))
    if user_level > int(summary["level"]):
        step.raise_to(user_level, "user_level")
    return step.update(summary=summary)


def _public_payload(summary: dict) -> dict:
    pub = summary["public"]
    return {"requisitions_by_dept": [{"dept": r["dept"], "open_headcount": r["open_headcount"],
                                      "roles": [{"title": x["title"], "status": x["status"]} for x in r["roles"]]}
                                     for r in pub["requisitions_by_dept"]],
            "staff_by_dept": pub["staff_by_dept"],
            "staff_denied": [d["dept"] for d in pub["staff_denied"]]}


@node(caps={"sensitive_data"})
async def note(state: HeadcountState, runtime: Any) -> dict:
    step = Step(runtime, state)
    summary = state.get("summary") or {}
    res = await step.llm([{"role": "system", "content": NOTE_SYSTEM},
                          {"role": "user", "content": json.dumps(_public_payload(summary), ensure_ascii=False)}],
                         purpose=_fake.HEADCOUNT_NOTE, max_tokens=250)
    text = (res.text or "").strip()
    source = "model"
    if not text or _DIGIT.search(text):
        text, source = CODE_NOTE, "code"  # a figure written by the model is never shown
    data = {"kind": "hr_headcount", "level": summary.get("level"), "public": summary.get("public"),
            "budget": summary.get("budget"), "note_source": source,
            "note_route": {"route": str(res.route), "level": int(res.level)}}
    return step.update(output={"text": text, "citations": [], "data": data})


def build_graph() -> StateGraph:
    _fake.install()
    g = StateGraph(HeadcountState, context_schema=RunContext)
    g.add_node("summarize", summarize)
    g.add_node("note", note)
    g.add_edge(START, "summarize")
    g.add_edge("summarize", "note")
    g.add_edge("note", END)
    return g
