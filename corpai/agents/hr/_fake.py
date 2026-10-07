"""FakeLLM responder for the headcount note (CORPAI_LLM=fake). It reads only the public part the graph
sent and writes no digits: the numbers are shown from the code-built table, never from model text."""

from __future__ import annotations

import json

HEADCOUNT_NOTE = "hr.headcount_note"


def headcount_note(messages: list[dict], *, route: str, json_mode: bool) -> str:
    data: dict = {}
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            try:
                data = json.loads(m["content"])
            except ValueError:
                data = {}
            break
    busiest = max(data.get("requisitions_by_dept") or [{}], key=lambda d: d.get("open_headcount") or 0)
    dept = busiest.get("dept") or "no department"
    return (f"[fake/{route}] Most open hiring is in {dept}. Departments under the minimum group size are "
            f"shown as hidden, and departments you may not see are listed as no permission.")


def install() -> None:
    from corpai.gateway import fake_llm

    fake_llm().register(HEADCOUNT_NOTE, headcount_note)
