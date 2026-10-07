"""The HTTP API (architecture §13, phase-B spec §18). Public API: create_app().

Milestone routes go in corpai/api/routes_<area>.py and are included by create_app().
"""

from __future__ import annotations

from corpai.api.app import create_app

__all__ = ["create_app"]
