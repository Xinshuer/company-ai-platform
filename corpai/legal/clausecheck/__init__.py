"""Clause Check - contract clause reviewer, vendored into corpai (see NOTICE.md)."""

from .config import Settings
from .models import ClauseReview, ClauseVerdict, Contract, ReviewReport
from .pipeline import review_contract
from .review import review_clause
from .split import contract_from_text, load_contract, split_clauses

__all__ = [
    "Settings",
    "ClauseReview",
    "ClauseVerdict",
    "Contract",
    "ReviewReport",
    "review_contract",
    "review_clause",
    "contract_from_text",
    "load_contract",
    "split_clauses",
]
