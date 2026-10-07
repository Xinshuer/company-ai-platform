"""Result types of the classifier.

A Finding records *where* something sensitive is, never *what* it is: findings end up in audit
details and traces, and copying the matched text there would leak exactly what we classified.
"""

from __future__ import annotations

from dataclasses import dataclass

from corpai.types import Level

# Kinds produced by classify_text (architecture §7) plus the L4 combination detectors.
KINDS: frozenset[str] = frozenset({
    "email", "phone", "iban", "card", "tax_id", "person_name", "salary", "money", "code", "secret",
    "canary", "health", "protected_attr", "performance", "vulnerability", "litigation",
    "classifier_error",
})


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    start: int  # offset into the classified text (characters)
    end: int  # exclusive
    level: Level


@dataclass(frozen=True, slots=True)
class Classification:
    level: Level
    findings: tuple[Finding, ...] = ()

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(f.kind for f in self.findings)


@dataclass(frozen=True, slots=True)
class DlpResult:
    blocked: bool
    level: Level
    findings: tuple[Finding, ...] = ()
