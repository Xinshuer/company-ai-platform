"""Table-driven classification (cases.yaml) plus properties every Finding must have."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from corpai.classify import Classification, Finding, classify_text
from corpai.classify.model import KINDS
from corpai.types import Level

CASES = yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text(encoding="utf-8"))


def test_case_table_is_broad():
    assert len(CASES) >= 80
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    by_level = {lvl: sum(1 for c in CASES if c["level"] == lvl) for lvl in ("L1", "L2", "L3", "L4")}
    assert all(n >= 5 for n in by_level.values()), by_level
    langs = {c["id"].split("-")[0] for c in CASES}
    assert {"en", "de", "zh"} <= langs


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    result = classify_text(case["text"])
    got = {(f.kind, case["text"][f.start:f.end]) for f in result.findings}
    assert result.level == Level[case["level"]], got
    assert set(case.get("kinds", [])) <= result.kinds, got
    assert not set(case.get("not_kinds", [])) & result.kinds, got


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_findings_hold_offsets_only(case):
    """Findings go to audit and traces: they must never carry the matched text."""
    text = case["text"]
    result = classify_text(text)
    for f in result.findings:
        assert [fl.name for fl in dataclasses.fields(f)] == ["kind", "start", "end", "level"]
        assert f.kind in KINDS
        assert 0 <= f.start <= f.end <= len(text)
        assert isinstance(f.level, Level)
        matched = text[f.start:f.end]
        if len(matched) >= 4:
            assert matched not in repr(f)
            assert matched not in repr(result)


def test_finding_has_no_room_for_text():
    f = Finding("email", 0, 5, Level.L3)
    with pytest.raises((AttributeError, TypeError)):
        f.text = "x@y.z"  # type: ignore[attr-defined]  # slots + frozen: nothing can be attached


def test_level_is_max_of_floor_and_findings():
    assert classify_text("hello", floor=Level.L3).level == Level.L3
    assert classify_text("hello", floor=2).level == Level.L2
    assert classify_text("hello", floor="L4").level == Level.L4
    # content above the floor wins; the floor never lowers anything
    assert classify_text("Anna Becker's salary is €78,000", floor=Level.L2).level == Level.L4


def test_rules_mix_languages():
    text = "Kurze Frage: 王伟 hat ein Gehalt von 70.000 €?"
    r = classify_text(text)
    assert r.level == Level.L4 and {"salary", "person_name"} <= r.kinds


def test_findings_sorted_and_classification_frozen():
    r = classify_text("Mail jane@example.com, call +49 30 1234567, IBAN DE89 3704 0044 0532 0130 00")
    assert [f.start for f in r.findings] == sorted(f.start for f in r.findings)
    assert isinstance(r, Classification)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.level = Level.L1  # type: ignore[misc]


def test_salary_needs_a_number_nearby():
    far = "Salary" + " filler" * 20 + " 78,000"
    assert "salary" not in classify_text(far).kinds
    assert "salary" in classify_text("Salary: 78,000").kinds


def test_money_commercial_window():
    assert classify_text("discount: €5,000").level == Level.L3
    assert classify_text("discount" + " x" * 60 + " €5,000").level == Level.L2


def test_performance_needs_a_person():
    assert classify_text("performance rating of 2").level < Level.L4
    assert classify_text("Marie Dubois: performance rating of 2").level == Level.L4


def test_vulnerability_and_litigation_need_company_context():
    assert classify_text("An exploit for an RCE was published.").level == Level.L1
    assert classify_text("An exploit for an RCE in fleetpilot was published.").level == Level.L4
    assert classify_text("The lawsuit was dismissed.").level == Level.L1
    assert classify_text("The lawsuit M-17 was dismissed.").level == Level.L4


def test_code_block_with_company_identifier_anywhere_inside():
    plain = "```\nfor i in range(3):\n    print(i)\n```"
    assert classify_text(plain).level == Level.L2
    ours = "```\nfor i in range(3):\n    print(i)  # see wiki.abctech.internal\n```"
    assert classify_text(ours).level == Level.L3
    unterminated = "```\nimport kb_auth\nkb_auth.login()"
    assert classify_text(unterminated).level == Level.L3
