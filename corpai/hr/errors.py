"""Errors of the HR domain logic."""

from __future__ import annotations


class NoPermission(PermissionError):
    """An explicit "no permission" (ENGINEERING rule 6). The message never says whether a hidden row
    exists ("no permission for this candidate (or it does not exist)")."""
