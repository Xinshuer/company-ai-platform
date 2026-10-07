"""Append-only, hash-chained audit log (docs/architecture.md §9). Public API re-exported here."""

from __future__ import annotations

from corpai.audit.anchor import Anchor, AnchorError, read_anchors, write_anchor
from corpai.audit.chain import ChainReport, verify_chain, verify_chain_sql
from corpai.audit.credentials import check_no_credentials, is_credential_key
from corpai.audit.log import EVENTS, arecord, read_log, record, record_owner_job

__all__ = [
    "EVENTS", "Anchor", "AnchorError", "ChainReport", "arecord", "check_no_credentials", "is_credential_key", "read_log",
    "read_anchors", "record", "record_owner_job", "verify_chain", "verify_chain_sql", "write_anchor",
]
