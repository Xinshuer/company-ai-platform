"""Errors of the sales domain logic."""

from __future__ import annotations


class NoPermission(PermissionError):
    """An explicit "no permission" (ENGINEERING rule 6): the caller is told, nothing is skipped silently.

    The message never says whether a hidden row exists ("no permission or no such account")."""
