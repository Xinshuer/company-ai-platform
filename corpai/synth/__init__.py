"""Synthetic company generator (architecture §15): `run(seed=7, small=False) -> SynthReport`.

Everything it produces is fictional. See docs/company.md for the story.
"""

from corpai.synth.generator import SynthReport, generate, run
from corpai.synth.personas import PERSONAS

__all__ = ["run", "generate", "SynthReport", "PERSONAS"]
