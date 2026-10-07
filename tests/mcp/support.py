"""Helpers shared by tests/mcp and tests/proposals: an app with every MCP server mounted, a client
that calls as a persona, owner-side counting, audit lookups and a test-only writer agent.

Two catalogue write tools (code.create_ticket / comment_ticket, finance.post_journal_entry) are
held by no agent in config/agents.yaml yet (issues-log D-06), so the tool-token path would refuse
them for everyone. `writer_agent` adds a test-only agent holding them; user rules still apply.
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from corpai.ids import new_id
from corpai.mcp_client import McpClient, ToolEnvelope

WRITER_AGENT = "b2_test_writer"


@asynccontextmanager
async def mcp_app() -> AsyncIterator[Any]:
    from starlette.applications import Starlette

    from corpai.mcp_servers import mount_all

    app = Starlette()
    async with mount_all(app):
        yield app


class Caller:
    def __init__(self, app: Any, personas: dict):
        self.client = McpClient(asgi_app=app)
        self.personas = personas

    async def __call__(self, persona: Any, agent: str, domain: str, tool: str, run_id: str | None = None,
                       **args: Any) -> ToolEnvelope:
        p = self.personas[persona] if isinstance(persona, str) else persona
        return await self.client.call(principal=p, agent=agent, run_id=run_id or new_id("run"), domain=domain,
                                      tool=tool, args=args)


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    from corpai.db import pg

    with pg.connect_owner() as conn:
        return conn.execute(sql, params).fetchall()


def owner_exec(sql: str, params: tuple = ()) -> None:
    from corpai.db import pg

    with pg.connect_owner() as conn:
        conn.execute(sql, params)


def count(table: str) -> int:
    return int(owner_rows(f"SELECT count(*) AS n FROM {table}")[0]["n"])


def audit_rows(run_id: str) -> list[dict]:
    return owner_rows("SELECT seq, actor, act_chain, event, action, resource, decision, reason, level, run_id,"
                      " approval_id, details::text AS details FROM audit.log WHERE run_id = %s ORDER BY seq", (run_id,))


def install_writer_agent(monkeypatch) -> None:
    from corpai.config import load_yaml
    from corpai.policy import agents

    raw = copy.deepcopy(load_yaml("agents"))
    raw["agents"][WRITER_AGENT] = {
        "description": "test only", "apps": [], "purpose": None,
        "start": {"roles": ["employee", "manager", "ceo"], "depts": []},
        "tools": ["code.create_ticket", "code.comment_ticket", "code.get_ticket", "finance.post_journal_entry"],
    }
    monkeypatch.setattr(agents, "load_yaml", lambda name: raw if name == "agents" else load_yaml(name))
