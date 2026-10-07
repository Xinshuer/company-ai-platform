"""The legal_review graph's shape (architecture §12 Rule of Two, design-review 二.6) and its input
rules, without the API.

- The router builds the real graph; every node declares its capabilities and no path joins
  untrusted input, sensitive data and an external action without the approval gate.
- The nodes that see the counterparty's text (intake, review) hold no external action; the nodes
  that file write tools (propose_send, propose_draft) never see untrusted input and are followed by
  the gate; the gate itself declares nothing.
- The review node's code calls no MCP tool, no write and nothing outbound.
- initial_state resets every run-scoped key, so a send run in a thread that held a review carries
  no contract text or review output; the input is parsed by fixed rules, never by a model.
"""

from __future__ import annotations

import inspect

import pytest

from corpai.agents import router
from corpai.agents._rule_of_two import graph_caps, undeclared_nodes, violations
from corpai.agents.legal import graph as legal_graph
from corpai.agents.legal import initial_state
from corpai.agents.legal._input import InputError, parse

pytestmark = pytest.mark.security

U, S, X = "untrusted_input", "sensitive_data", "external_action"


def _graph():
    return router.buildable_apps()["legal_review"]()


def test_legal_review_is_built_by_the_router_and_keeps_the_rule_of_two():
    g = _graph()
    assert undeclared_nodes(g) == []
    assert [str(v) for v in violations(g)] == []
    assert router.app_ready("legal_review")


def test_untrusted_nodes_cannot_act_and_acting_nodes_never_see_untrusted_text():
    caps = graph_caps(_graph())
    assert caps["intake"] == (frozenset({U, S}), False)
    assert caps["review"] == (frozenset({U, S}), False)
    for name in ("propose_send", "propose_draft"):
        assert caps[name] == (frozenset({S, X}), False), name
    assert caps["approval"] == (frozenset(), True)
    assert all(X not in c for n, (c, _) in caps.items() if U in c)
    assert all(U not in c for n, (c, _) in caps.items() if X in c)


def test_every_write_node_is_followed_by_the_gate():
    g = _graph()
    for src in ("propose_send", "propose_draft"):
        ends = set(g.branches[src]["_to_gate"].ends.values())
        assert ends == {"approval", "__end__"}, src
    assert ("approval", "finish") in g.edges


def test_review_node_code_calls_no_tool_and_nothing_outbound():
    src = inspect.getsource(legal_graph.review)
    for forbidden in ("call_tool", ".llm(", "send_contract", "create_contract_draft", "approval_gate", "mcp"):
        assert forbidden not in src, forbidden
    intake_src = inspect.getsource(legal_graph.intake)
    assert intake_src.count("call_tool(") == 1 and '"get_contract"' in intake_src  # one read, by id


def test_a_send_run_starts_without_any_contract_text_or_review():
    send = initial_state('{"mode": "send", "contract_id": "C-0004", "to": "aime.breton@rocheval-transports.example"}')
    assert send["contract_text"] == ""
    for key in ("review", "doc", "draft", "proposal", "decision", "output"):
        assert key in send and send[key] is None, key
    assert set(send["request"]) == {"mode", "contract_id", "to", "note"}
    review = initial_state('{"mode": "review", "contract_id": "C-0004", "text": "Clause 1. Something."}')
    assert review["contract_text"] == "Clause 1. Something." and "text" not in review["request"]


@pytest.mark.parametrize("text,expect", [
    ("review C-0004", {"mode": "review", "contract_id": "C-0004", "text": "", "upload_id": None}),
    ("审查 C-0005（Wartopol MSA）", {"mode": "review", "contract_id": "C-0005", "text": "", "upload_id": None}),
    ("下个月到期的合同有哪些？", {"mode": "due", "month": None}),
    ("Which contracts expire in 2026-12?", {"mode": "due", "month": "2026-12"}),
    ('{"mode": "due"}', {"mode": "due", "month": None}),
    ('{"mode": "review", "upload_id": "upl_01abcdefghjkmnpqrstv"}',
     {"mode": "review", "contract_id": None, "text": "", "upload_id": "upl_01abcdefghjkmnpqrstv"}),
    ('{"mode": "send", "contract_id": "C-0004", "to": "a@b.example"}',
     {"mode": "send", "contract_id": "C-0004", "to": "a@b.example", "note": ""}),
])
def test_input_rules(text, expect):
    assert parse(text) == expect


def test_long_plain_text_is_a_pasted_contract():
    body = "1. Services. " + "The supplier provides route planning. " * 10
    assert parse(body) == {"mode": "review", "contract_id": None, "text": body.strip(), "upload_id": None}


@pytest.mark.parametrize("text", [
    "hello", "", "   ", "C-0004 and C-0005", "[1, 2]", "{not json",
    '{"mode": "review"}', '{"mode": "review", "contract_id": "C-4"}',
    '{"mode": "review", "contract_id": "C-0004", "user_id": "u0030"}',  # no field names a principal
    '{"mode": "review", "upload_id": "upl_01abcdefghjkmnpqrstv", "text": "x"}',
    '{"mode": "send", "contract_id": "C-0004"}', '{"mode": "send", "contract_id": "C-0004", "to": "x"}',
    '{"mode": "send", "contract_id": "C-0004", "to": "a@b.example", "subject": "set by caller"}',
    '{"mode": "draft", "template_type": "NDA"}', '{"mode": "due", "month": "next"}',
    '{"mode": "approve", "proposal_id": "prop_x"}',
])
def test_bad_input_is_refused_with_a_message(text):
    with pytest.raises(InputError) as e:
        parse(text)
    assert str(e.value)
    state = initial_state(text)
    assert state["request"] == {"mode": "invalid"} and state["input_error"]
