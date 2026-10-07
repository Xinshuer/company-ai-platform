"""The OpenAI-compatible path: Clause Check's own OpenAICompatBackend, unchanged, against the gateway's
/v1/chat/completions served by uvicorn on 127.0.0.1, authenticated with a gateway token (floor L3)."""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest
from fastapi import FastAPI

from corpai.gateway import openai_compat
from corpai.identity.tokens import mint_gateway_token
from corpai.legal import load_playbook, register_compat_responder
from corpai.legal.clausecheck import Settings
from corpai.legal.clausecheck.review import OpenAICompatBackend, review_clause
from corpai.legal.clausecheck.split import contract_from_text
from corpai.types import Level


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def compat_server(seeded):
    import uvicorn

    app = FastAPI()
    app.include_router(openai_compat.router)
    port = _free_port()
    # loop="none" + a Selector loop: psycopg async cannot run on Windows' default Proactor loop.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, loop="none", lifespan="off",
                                           log_level="warning", access_log=False))
    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop), daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("compat server did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=20)


def test_clause_check_backend_runs_unchanged_through_the_gateway(compat_server, personas, contracts, fake_llm,
                                                                 run_id, owner_query, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    prefix = register_compat_responder(fake_llm)
    try:
        token = mint_gateway_token(personas["legal_staff"], agent="legal_review", run_id=run_id, floor=Level.L3)
        settings = Settings(mode="local", local_base_url=compat_server, local_api_key=token)
        assert settings.compat
        contract = contract_from_text(contracts["C-0003"]["text"], name="C-0003")
        review = review_clause(contract, contract.get("3"), load_playbook().clausecheck, settings)
        # review_clause picks the vendored, unchanged OpenAICompatBackend for mode "local"
        # (test_vendored.py pins its source to the original commit)
        assert OpenAICompatBackend.__module__ == "corpai.legal.clausecheck.review"
    finally:
        fake_llm.unregister(prefix)
    assert review.trace.error is None, review.trace.error
    assert review.verdict.verdict == "accept" and review.verdict.rule_ids == ["KB-02"]
    # the call went through the gateway: audited, as this user and agent, routed local at the token's floor
    rows = owner_query("SELECT actor, act_chain, route, level, decision, details FROM audit.log "
                       "WHERE run_id = %s AND event = 'model_call'", (run_id,))
    assert rows, "no model_call audited for the compat run"
    for r in rows:
        assert (r["actor"], r["act_chain"], r["route"], r["decision"]) == ("u0031", ["agent:legal_review"], "local",
                                                                           "allow")
        assert r["level"] >= 3 and r["details"]["purpose"] == "compat.legal_review"
    assert [c["route"] for c in fake_llm.calls] == ["local"] * len(rows)
    assert len(rows) == review.trace.steps


def test_compat_path_rejects_a_bad_token(compat_server, contracts, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    settings = Settings(mode="local", local_base_url=compat_server, local_api_key="not-a-token")
    contract = contract_from_text(contracts["C-0003"]["text"], name="C-0003")
    review = review_clause(contract, contract.get("3"), load_playbook().clausecheck, settings)
    # Clause Check turns the 401 into an error step and a "needs human review" flag; nothing is guessed
    assert review.trace.error and "401" in review.trace.error
    assert review.verdict.verdict == "flag" and review.verdict.confidence == "low"
    assert "not-a-token" not in review.trace.error
