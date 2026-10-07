"""Versioned department playbooks (phase-B spec). Public API: load_playbook(id) -> Playbook (active
version; `.ref` = "<id>@<version>" for outputs that used it), load_versions, list_playbooks,
Playbook, PlaybookError, PlaybookNotFound."""

from __future__ import annotations

from corpai.playbooks.loader import (
    PLAYBOOK_DIR,
    STATUSES,
    Playbook,
    PlaybookError,
    PlaybookNotFound,
    list_playbooks,
    load_playbook,
    load_versions,
)

__all__ = ["PLAYBOOK_DIR", "STATUSES", "Playbook", "PlaybookError", "PlaybookNotFound", "list_playbooks",
           "load_playbook", "load_versions"]
