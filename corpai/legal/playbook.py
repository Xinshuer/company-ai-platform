"""ABC Tech's legal playbook: Clause Check's "rules as data", from the provider's side.

`data/playbook.json` uses Clause Check's PlaybookRule format (so the vendored tools serve it to
the model unchanged) plus fields only corpai reads:

- `clause_type`: the `legal.clause_library` type that holds the preferred / fallback wording;
- `fallback_flags`, `numeric` (with `red_flags`): deterministic checks that place a clause at
  preferred / fallback / outside. The offline reviewer uses them, and review.py uses them as a
  floor: a model may be stricter, never more lenient (a hijacked model cannot accept "unlimited
  liability");
- `required` + `presence`: terms the contract must contain at all. "Missing" is decided by code,
  because a model reviewing one clause at a time cannot see what is absent from the whole text.

The playbook is L3 (design-review 二.1: the negotiation position is confidential), so every call
that carries it goes to the `local` route.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from corpai.legal.clausecheck.models import PlaybookRule
from corpai.legal.clausecheck.playbook import Playbook
from corpai.types import Level

PLAYBOOK_PATH = Path(__file__).resolve().parent / "data" / "playbook.json"

# Worst-first ordering of where a clause sits relative to a rule.
POSITION_RANK = {"preferred": 0, "not_applicable": 0, "fallback": 1, "outside": 2}
REQUIRED_KINDS = ("never", "always", "commercial", "personal_data")
COMMERCIAL_TYPES = frozenset({"MSA", "order_form", "SOW"})
_COMMERCIAL_HINT = re.compile(r"\bfees?\b|software-as-a-service|\bsubscription\b|\bbilled\b", re.I)
_PERSONAL_DATA_HINT = re.compile(r"personal data|data protection|\bprocessor\b|driver names", re.I)


@dataclass(frozen=True)
class NumericCheck:
    pattern: re.Pattern[str]
    unit: str
    preferred: tuple[float, float]
    fallback: tuple[float, float]

    def place(self, value: float) -> str:
        if self.preferred[0] <= value <= self.preferred[1]:
            return "preferred"
        if self.fallback[0] <= value <= self.fallback[1]:
            return "fallback"
        return "outside"


@dataclass(frozen=True)
class KbRule:
    id: str
    topic: str
    title: str
    severity: str
    clause_type: str | None
    keywords: tuple[str, ...]
    red_flags: tuple[re.Pattern[str], ...]
    fallback_flags: tuple[re.Pattern[str], ...]
    numeric: tuple[NumericCheck, ...]
    required: str
    presence: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class KbPlaybook:
    id: str
    version: str
    level: Level
    rules: tuple[KbRule, ...]
    clausecheck: Playbook = field(repr=False)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    def rule_ref(self, rule_id: str) -> str:
        return f"{rule_id}@{self.version}"

    def get(self, rule_id: str) -> KbRule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def for_topic(self, topic: str) -> list[KbRule]:
        return [r for r in self.rules if r.topic == topic]


def _compile(patterns: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.I) for p in patterns)


def _rule(raw: dict) -> KbRule:
    required = raw.get("required", "never")
    if required not in REQUIRED_KINDS:
        raise ValueError(f"playbook rule {raw.get('id')}: unknown 'required' value {required!r}")
    numeric = tuple(
        NumericCheck(re.compile(n["pattern"], re.I), n["unit"], tuple(n["preferred"]), tuple(n["fallback"]))
        for n in raw.get("numeric", []))
    return KbRule(
        id=raw["id"], topic=raw["topic"], title=raw["title"], severity=raw.get("severity", "medium"),
        clause_type=raw.get("clause_type"), keywords=tuple(k.lower() for k in raw.get("keywords", [])),
        red_flags=_compile(raw.get("red_flags", [])), fallback_flags=_compile(raw.get("fallback_flags", [])),
        numeric=numeric, required=required, presence=_compile(raw.get("presence", [])))


@lru_cache(maxsize=4)
def load_playbook(path: str | None = None) -> KbPlaybook:
    p = Path(path) if path else PLAYBOOK_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    rules = tuple(_rule(r) for r in data["rules"])
    # The vendored Playbook validates every rule against Clause Check's schema (unknown topics fail)
    # and serves lookup_playbook to the model; PlaybookRule ignores the corpai-only keys.
    cc = Playbook([PlaybookRule(**r) for r in data["rules"]])
    if len({r.id for r in rules}) != len(rules):
        raise ValueError("playbook rule ids must be unique")
    return KbPlaybook(id=data["id"], version=data["version"], level=Level(int(data["level"])), rules=rules,
                      clausecheck=cc)


# ---- deterministic assessment (offline reviewer, eval harness) ------------------------------

@dataclass(frozen=True)
class RuleAssessment:
    rule: KbRule
    position: str  # preferred | fallback | outside
    reason: str
    span: tuple[int, int] | None  # offsets into the assessed text of the deciding match
    score: int


def relevant_rules(playbook: KbPlaybook, text: str, section_path: str = "") -> list[tuple[KbRule, int]]:
    """Rules whose keywords appear in the clause; the heading breadcrumb counts double (as in
    Clause Check's mock reviewer: "8 Governing law and venue" says more than the body does)."""
    low, path = text.lower(), section_path.lower()
    out = []
    for rule in playbook.rules:
        if not rule.keywords:
            continue
        score = sum(1 for k in rule.keywords if k in low) + 2 * sum(1 for k in rule.keywords if k in path)
        if score:
            out.append((rule, score))
    return out


def assess_rule(rule: KbRule, text: str, score: int = 0) -> RuleAssessment:
    for pat in rule.red_flags:
        m = pat.search(text)
        if m:
            return RuleAssessment(rule, "outside", f'never-accept wording "{m.group(0)}"', m.span(), score)
    worst: RuleAssessment | None = None
    for check in rule.numeric:
        for m in check.pattern.finditer(text):
            try:
                value = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            pos = check.place(value)
            cand = RuleAssessment(rule, pos, f"{m.group(1)} {check.unit} is {pos} for {rule.title.lower()}",
                                  m.span(), score)
            if worst is None or POSITION_RANK[pos] > POSITION_RANK[worst.position]:
                worst = cand
    if worst is not None and worst.position != "preferred":
        return worst
    for pat in rule.fallback_flags:
        m = pat.search(text)
        if m:
            return RuleAssessment(rule, "fallback", f'fallback wording "{m.group(0)}"', m.span(), score)
    if worst is not None:
        return worst
    return RuleAssessment(rule, "preferred", "no wording outside the preferred position", None, score)


def assess_clause(playbook: KbPlaybook, text: str, section_path: str = "") -> list[RuleAssessment]:
    """Every relevant rule's assessment, worst first (then by keyword score)."""
    out = [assess_rule(rule, text, score) for rule, score in relevant_rules(playbook, text, section_path)]
    out.sort(key=lambda a: (-POSITION_RANK[a.position], -a.score, a.rule.id))
    return out


def rule_applies(rule: KbRule, text: str, *, contract_type: str | None, personal_data: bool | None) -> bool:
    if rule.required == "always":
        return True
    if rule.required == "commercial":
        if contract_type is not None:
            return contract_type in COMMERCIAL_TYPES
        return bool(_COMMERCIAL_HINT.search(text))
    if rule.required == "personal_data":
        if personal_data is not None:
            return personal_data
        return bool(_PERSONAL_DATA_HINT.search(text))
    return False


def missing_rules(playbook: KbPlaybook, text: str, *, contract_type: str | None = None,
                  personal_data: bool | None = None, dpa_in_place: bool = False) -> list[KbRule]:
    """Required terms that appear nowhere in the contract. Known contract fields (from the
    contract store) decide whether a rule applies; for uploaded text, the text itself does.

    `dpa_in_place`: the contract store shows Article 28 terms outside this text (a signed DPA with
    the same customer, or a recorded DPA clause of the stored contract). The personal-data rules are
    then satisfied, as in the contracts_missing_dpa metric and the missing_dpa_clause signal."""
    return [r for r in playbook.rules
            if r.required != "never" and r.presence
            and not (dpa_in_place and r.required == "personal_data")
            and rule_applies(r, text, contract_type=contract_type, personal_data=personal_data)
            and not any(p.search(text) for p in r.presence)]
