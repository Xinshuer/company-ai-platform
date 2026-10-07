"""M4 HR: screening evidence (D6), the human decision (GDPR Art. 22), headcount summary (L2 vs L3), bias checks.

Public API (the wiring phase calls these from the hr_screening / hr_headcount graphs and API routes):
    async screen_candidate(p, *, candidate_id, run_id) -> ScreeningEvidence      evidence only; stored, L4
    async record_decision(p, *, candidate_id, decision, reason) -> screening_id  human action; HR on the req
    async headcount_summary(p, *, run_id=None) -> {"public" (L2), "budget" (L3), "level"}
    prepare_resume(text, *, name, email=None, phone=None) -> PreparedResume       what the model sees
    compare_pair(a, b), group_difference_report(results, *, attribute)           bias checks
    ScreeningEvidence, RequirementEvidence, Verdict, NoPermission
    register_fake_responders()                                                   FakeLLM 'hr.' (done on import)
"""

from __future__ import annotations

from corpai.hr.bias import compare_pair, group_difference_report, verdicts
from corpai.hr.decisions import DECISIONS, record_decision
from corpai.hr.errors import NoPermission
from corpai.hr.evidence import RequirementEvidence, ScreeningEvidence, Verdict
from corpai.hr.fake import register_fake_responders
from corpai.hr.headcount import headcount_summary
from corpai.hr.prepare import PreparedResume, prepare_resume
from corpai.hr.screening import screen_candidate

register_fake_responders()

__all__ = [
    "DECISIONS", "NoPermission", "PreparedResume", "RequirementEvidence", "ScreeningEvidence", "Verdict",
    "compare_pair", "group_difference_report", "headcount_summary", "prepare_resume", "record_decision",
    "register_fake_responders", "screen_candidate", "verdicts",
]
