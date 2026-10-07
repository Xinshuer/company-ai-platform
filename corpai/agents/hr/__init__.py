"""M4 HR graphs. One module serves three apps, so each has its own builder name (the router looks for
build_<app>_graph / <app>_initial_state before build_graph):

    hr_headcount    headcount_summary (L2 public part vs L3 budget part), a digit-free note
    hr_screening    evidence per hard requirement, then the HR person's decision (interrupt)
    hr_candidates   the hiring manager's read-only view of their requisitions' candidates
    decide(...)     record the human decision and resume the waiting screening thread
"""

from __future__ import annotations

from corpai.agents.hr.candidates import collect as collect_candidates
from corpai.agents.hr.candidates import build_graph as build_hr_candidates_graph
from corpai.agents.hr.candidates import initial_state as hr_candidates_initial_state
from corpai.agents.hr.decision import DecisionRefused, decide
from corpai.agents.hr.headcount import build_graph as build_hr_headcount_graph
from corpai.agents.hr.headcount import initial_state as hr_headcount_initial_state
from corpai.agents.hr.screening import build_graph as build_hr_screening_graph
from corpai.agents.hr.screening import initial_state as hr_screening_initial_state

__all__ = [
    "DecisionRefused", "build_hr_candidates_graph", "build_hr_headcount_graph", "build_hr_screening_graph",
    "collect_candidates", "decide", "hr_candidates_initial_state", "hr_headcount_initial_state",
    "hr_screening_initial_state",
]
