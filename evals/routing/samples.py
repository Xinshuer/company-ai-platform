"""The design-review 四.3 routing samples (routing_samples.yaml) and how to run one.

Shared by tests/security/test_routing_samples.py (every row holds) and
evals/routing/test_routing_robustness.py (the rows survive obfuscated text).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from corpai.classify import source_default
from corpai.gateway import ChatRequest
from corpai.ids import new_id
from corpai.types import Level, Route

SAMPLES: list[dict] = yaml.safe_load(Path(__file__).with_name("routing_samples.yaml").read_text(encoding="utf-8"))[
    "samples"]

_PLACEHOLDER = re.compile(r"\{(contact|candidate|employee):([A-Za-z0-9-]+)\}")
_NAME_SQL = {"contact": "SELECT name FROM crm.contacts WHERE account_id = %s ORDER BY contact_id LIMIT 1",
             "candidate": "SELECT name FROM hr.candidates WHERE candidate_id = %s",
             "employee": "SELECT name FROM core.users WHERE user_id = %s"}


def fill(text: str) -> str:
    """Replace {contact:A-…}, {candidate:CAND-…}, {employee:u…} with names from the synthetic company
    (owner connection: the samples are test data, not a request path)."""
    from corpai.db import pg

    def name(m: re.Match) -> str:
        with pg.connect_owner() as conn:
            row = conn.execute(_NAME_SQL[m.group(1)], (m.group(2),)).fetchone()
        assert row, m.group(0)
        return row["name"]

    return _PLACEHOLDER.sub(name, text)


def sample_hwm(sample: dict) -> Level:
    """What the runtime sets before the call: the highest default level of the attached sources and
    playbooks (rule 4), L1 when nothing internal is attached."""
    levels = [int(source_default(s)) for s in sample.get("sources") or []]
    pb = sample.get("playbook")
    if pb == "legal":
        from corpai.legal import load_playbook as legal_playbook

        levels.append(int(legal_playbook().level))
    elif pb:
        from corpai.playbooks.loader import load_playbook

        levels.append(int(load_playbook(pb).level))
    return Level(max([int(Level.L1), *levels]))


def holds(sample: dict, route: Route | str, level: Level | int) -> bool:
    """The table's route, and for local rows at least its level; external rows at most L2."""
    if str(route) != sample["route"]:
        return False
    return int(level) >= sample["level"] if sample["route"] == "local" else int(level) <= Level.L2


async def ask(gateway, principal, text: str, hwm: Level = Level.L1, *, agent: str = "qa"):
    req = ChatRequest(messages=[{"role": "system", "content": "You are Kestrelbyte's internal assistant."},
                                {"role": "user", "content": text}],
                      principal=principal, hwm=hwm, purpose="qa.answer", run_id=new_id("run"), agent=agent)
    return await gateway.chat(req)
