"""M2 legal domain logic: contract review (Clause Check through the gateway), drafting from the
clause library, and contract expiry by SQL. Plain async service functions; the wiring phase calls
them from the legal_review graph, the API and the UI.

Public API:
    review_contract(principal, *, text, contract_id, run_id) -> ReviewResult
    draft_contract(principal, *, template_type, variables, run_id, instructions="") -> DraftResult
    contracts_due(principal, *, month=None) -> list[dict]
    list_reviews(principal, *, contract_id=None), get_review(principal, review_id)
    GatewayBackend, load_playbook, find_embedded_instructions
    REVIEW_CAPS / DRAFT_CAPS / DUE_CAPS   Rule-of-Two capabilities of the nodes that call them
    register_fake_responders()           done on import (FakeLLM: 'legal.review', 'legal.draft')
"""

from __future__ import annotations

from corpai.legal.backend import AGENT, PURPOSE, READ_ONLY_TOOLS, GatewayBackend
from corpai.legal.draft import TEMPLATES, DraftRejected, draft_contract
from corpai.legal.due import contracts_due, month_bounds
from corpai.legal.fake import register_compat_responder, register_fake_responders
from corpai.legal.injection import InstructionHit, find_embedded_instructions
from corpai.legal.playbook import KbPlaybook, load_playbook
from corpai.legal.results import ClauseResult, DraftResult, Finding, ReviewResult, Suggestion
from corpai.legal.review import ReviewRouteError, get_review, list_reviews, review_contract

# Capabilities for @node(caps=...) (architecture §12, Rule of Two). The review reads untrusted
# counterparty text and confidential data but has no external action; sending a contract is a
# separate node behind a four-eyes approval gate.
REVIEW_CAPS = frozenset({"untrusted_input", "sensitive_data"})
DRAFT_CAPS = frozenset({"sensitive_data"})
DUE_CAPS = frozenset({"sensitive_data"})

register_fake_responders()

__all__ = [
    "AGENT", "PURPOSE", "READ_ONLY_TOOLS", "GatewayBackend",
    "TEMPLATES", "DraftRejected", "draft_contract",
    "contracts_due", "month_bounds",
    "register_compat_responder", "register_fake_responders",
    "InstructionHit", "find_embedded_instructions",
    "KbPlaybook", "load_playbook",
    "ClauseResult", "DraftResult", "Finding", "ReviewResult", "Suggestion",
    "ReviewRouteError", "get_review", "list_reviews", "review_contract",
    "REVIEW_CAPS", "DRAFT_CAPS", "DUE_CAPS",
]
