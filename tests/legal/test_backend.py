"""GatewayBackend: hwm floor L3 (route local), fixed purpose/agent, read-only tools, fail-closed errors."""

from __future__ import annotations

import ast
from pathlib import Path

import anyio.to_thread
import pytest

from corpai.gateway import LocalUnavailable, ops
from corpai.legal import READ_ONLY_TOOLS, GatewayBackend, load_playbook, review_contract
from corpai.legal.clausecheck import Settings
from corpai.legal.clausecheck.review import model_review
from corpai.legal.clausecheck.split import contract_from_text
from corpai.types import Level

LEGAL = Path(__file__).resolve().parents[2] / "corpai" / "legal"
PUBLIC_CLAUSE = "Terms\n\n## 1. Purpose\nThis page describes FleetPilot, a route planning product.\n"


@pytest.mark.anyio
@pytest.mark.parametrize("asked", [Level.L1, Level.L2, 1, 0])
async def test_hwm_is_never_below_l3_so_the_route_is_local(personas, fake_llm, run_id, asked):
    contract = contract_from_text(PUBLIC_CLAUSE)

    def run():
        be = GatewayBackend(contract, principal=personas["legal_staff"], run_id=run_id, hwm=asked)
        assert be.hwm == Level.L3
        return be, model_review(be, contract, contract.reviewable[0], load_playbook().clausecheck,
                                Settings(mode="gateway"))

    be, review = await anyio.to_thread.run_sync(run)
    assert review.trace.error is None and be.calls
    assert all(c.route == "local" and c.level >= 3 for c in be.calls)
    assert [(c["purpose"], c["route"]) for c in fake_llm.calls] == [("legal.review", "local")] * len(be.calls)
    for c in be.calls:
        assert set(c.tools_offered) <= READ_ONLY_TOOLS
    assert READ_ONLY_TOOLS == {"lookup_playbook", "get_clause", "propose_redline", "mark_for_review"}


def test_purpose_must_stay_in_the_legal_namespace(personas):
    with pytest.raises(ValueError):
        GatewayBackend(contract_from_text(PUBLIC_CLAUSE), principal=personas["legal_staff"], run_id="r",
                       hwm=Level.L3, purpose="qa.answer")


@pytest.mark.anyio
async def test_local_down_fails_the_review_instead_of_degrading(personas, contracts, fake_llm, run_id, owner_query):
    admin = personas["platform_admin"]
    await ops.set_switch(admin, "local_down", True, "legal test: local maintenance")
    try:
        with pytest.raises(LocalUnavailable):
            await review_contract(personas["legal_staff"], text=contracts["C-0003"]["text"], contract_id="C-0003",
                                  run_id=run_id)
    finally:
        await ops.set_switch(admin, "local_down", False, "legal test done")
    assert fake_llm.calls == []  # nothing reached any backend, least of all external
    rows = owner_query("SELECT route, decision FROM audit.log WHERE run_id = %s AND event = 'model_call'", (run_id,))
    assert rows and {r["route"] for r in rows} == {"local"} and {r["decision"] for r in rows} == {"deny"}
    stored = owner_query("SELECT count(*) AS n FROM legal.reviews WHERE run_id = %s", (run_id,))
    assert stored == [{"n": 0}]


_FORBIDDEN = {"corpai.mcp_client", "corpai.mcp_servers", "corpai.proposals", "corpai.agents", "requests", "httpx",
              "httpx2", "urllib", "socket", "smtplib", "subprocess"}


def test_domain_code_has_no_write_or_outbound_capability():
    """The legal service modules import nothing that can call tools, create proposals or open a
    connection of their own (the vendored OpenAICompatBackend is only used by the /v1 proof)."""
    for path in LEGAL.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        bad = {n for n in names if any(n == f or n.startswith(f + ".") for f in _FORBIDDEN)}
        assert not bad, (path.name, bad)
