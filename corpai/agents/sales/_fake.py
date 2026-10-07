"""FakeLLM responders for the sales_profile graph's own model calls (CORPAI_LLM=fake: tests, demo).

`sales.strategy` and `sales.followup_email` read only the JSON the graph put in the user message
(profile enums, scores, the playbook tier, CRM names) and answer deterministically, like a careful
model that never invents commitments. Registered under longer prefixes than corpai.sales's
'sales.' fallback, so they win; harmless in live mode, where the FakeLLM is never used.
"""

from __future__ import annotations

import json
from typing import Any

STRATEGY = "sales.profile_strategy"  # not "sales.strategy": corpai.sales keeps that as its safe fallback
FOLLOWUP = "sales.followup_email"


def _payload(messages: list[dict]) -> dict[str, Any]:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            try:
                data = json.loads(m["content"])
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return {}


def strategy(messages: list[dict], *, route: str, json_mode: bool) -> str:
    d = _payload(messages)
    pb = d.get("playbook") or {}
    steps = "; ".join(str(s) for s in (pb.get("next_steps") or [])[:3]) or "keep in touch"
    allowed = ", ".join(str(s) for s in (pb.get("allowed_commitments") or [])[:3]) or "nothing beyond the price list"
    return (f"[fake/{route}] {d.get('account') or 'The account'} is a {d.get('tier', 'low')}-tier opportunity "
            f"(fit {d.get('fit')}, intent {d.get('intent')}). Goal: {pb.get('goal', 'stay visible')}. "
            f"Next steps: {steps}. Contact every {pb.get('cadence_days', '?')} days. "
            f"You may offer: {allowed}.")


def followup_email(messages: list[dict], *, route: str, json_mode: bool) -> str:
    d = _payload(messages)
    pb = d.get("playbook") or {}
    step = (pb.get("next_steps") or ["a short call"])[0]
    contact = d.get("contact_name") or "there"
    body = (f"Dear {contact},\n\nthank you for your time with Kestrelbyte. As a next step I would like to "
            f"suggest {step}. Would one of the coming weeks suit you?\n\nKind regards")
    return json.dumps({"subject": f"Next step with FleetPilot for {d.get('account') or 'your team'}",
                       "body": body}, ensure_ascii=False)


def install() -> None:
    from corpai.gateway import fake_llm

    fake = fake_llm()
    fake.register(STRATEGY, strategy)
    fake.register(FOLLOWUP, followup_email)
