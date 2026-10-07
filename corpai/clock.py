"""Simulation clock. Business logic asks this module for "today", never the OS.

The synthetic company's data ends on 2026-09-30 and the simulation date is fixed, so
"contracts expiring next month" and other planted issues give the same answer every run.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from corpai.config import settings


def today() -> date:
    return settings().sim_today


def now_utc() -> datetime:
    """Wall-clock time for logs, audit and traces (not for business dates)."""
    return datetime.now(timezone.utc)
