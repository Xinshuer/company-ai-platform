"""redact_protected: résumés lose protected attributes before any model sees them (decision D6)."""

from __future__ import annotations

import dataclasses

import pytest

from corpai.classify import redact_protected

CV_EN = """Anna Becker
Senior Backend Engineer
[Photo]
Date of birth: 14.02.1990 | Nationality: German | Place of birth: Hamburg
Marital status: married, two children
Religion: Catholic
Health: no restrictions

Experience
- 2019-2026 Backend engineer at Nordhafen Logistik: Python, PostgreSQL, Kafka.
  She led a team of five and introduced contract testing.
"""

CV_DE = """Lebenslauf
Name: Jonas Weber
Geburtsdatum: 03.07.1985, geboren in Köln
Staatsangehörigkeit: deutsch
Familienstand: verheiratet, 2 Kinder
Konfession: evangelisch
Schwerbehinderung: GdB 50
Berufserfahrung: 8 Jahre Java und Kotlin
"""

CV_ZH = """王伟
性别：男  年龄：32岁  民族：汉族  籍贯：湖南长沙
婚姻状况：已婚已育  政治面貌：群众
照片：见附件
工作经历：2018-2026 某物流公司 后端开发，熟悉 Go 与 PostgreSQL。
"""


def _kinds(findings) -> set[str]:
    return {f.kind for f in findings}


def test_english_cv():
    out, findings = redact_protected(CV_EN)
    for leaked in ("14.02.1990", "German", "Hamburg", "married", "two children", "Catholic",
                   "no restrictions", "[Photo]", "She "):
        assert leaked not in out, leaked
    assert {"photo", "date_of_birth", "nationality", "origin", "marital_status", "religion", "health",
            "gender"} <= _kinds(findings)
    # the evidence the screening needs is still there
    for kept in ("Senior Backend Engineer", "Python, PostgreSQL, Kafka", "led a team of five"):
        assert kept in out
    assert "⟨removed:date_of_birth⟩" in out


def test_german_cv():
    out, findings = redact_protected(CV_DE)
    for leaked in ("03.07.1985", "Köln", "deutsch", "verheiratet", "2 Kinder", "evangelisch", "GdB 50"):
        assert leaked not in out, leaked
    assert "8 Jahre Java und Kotlin" in out
    assert {"date_of_birth", "nationality", "marital_status", "religion", "disability"} <= _kinds(findings)


def test_chinese_cv():
    out, findings = redact_protected(CV_ZH)
    for leaked in ("男", "32岁", "汉族", "湖南长沙", "已婚已育", "见附件"):
        assert leaked not in out, leaked
    assert "熟悉 Go 与 PostgreSQL" in out
    assert {"gender", "age", "ethnicity", "origin", "marital_status", "photo"} <= _kinds(findings)


@pytest.mark.parametrize("snippet,leak,kind", [
    ("Born on 12 March 1990 in Lyon, France.", "1990", "date_of_birth"),
    ("Born on 12 March 1990 in Lyon, France.", "Lyon", "date_of_birth"),
    ("I am 41 years old and live in Berlin.", "41 years", "age"),
    ("Aged 52, looking for a new challenge.", "52", "age"),
    ("Ich bin 38 Jahre alt.", "38 Jahre", "age"),
    ("Originally from Izmir, now based in Munich.", "Izmir", "origin"),
    ("Polish citizen with EU work permit.", "Polish citizen", "nationality"),
    ("Mother of three, returning after parental leave.", "Mother of three", "children"),
    ("Diagnosed with type 1 diabetes in 2015, fully fit for work.", "type 1 diabetes", "health"),
    ("Currently pregnant.", "pregnant", "health"),
    ("Wheelchair user; office must be accessible.", "Wheelchair", "disability"),
    ("Practising Muslim.", "Muslim", "religion"),
    ("Gender: female", "female", "gender"),
    ("Ms. Dubois has 6 years of Kotlin.", "Ms.", "gender"),
    ("![portrait](photo.jpg) Senior engineer", "photo.jpg", "photo"),
    ("<img src='me.png'> Engineer", "me.png", "photo"),
    ("Foto anbei. Erfahrung mit Rust.", "Foto anbei", "photo"),
    ("出生于1992年5月，现居上海。", "1992", "date_of_birth"),
    ("2018年怀孕，休产假一年。", "怀孕", "health"),
    ("育有一子。", "育有一子", "children"),
])
def test_inline_snippets(snippet, leak, kind):
    out, findings = redact_protected(snippet)
    assert leak not in out, out
    assert kind in _kinds(findings)


def test_counterfactual_pair_is_identical_after_redaction():
    """Same CV, different gender and age: the model must receive the same text (D6 bias test)."""
    a = ("Candidate: Alex Morgan\nGender: female\nAge: 29\n"
         "Summary: She has 6 years of Python. Her last project was a routing engine. 29 years old.\n")
    b = ("Candidate: Alex Morgan\nGender: male\nAge: 51\n"
         "Summary: He has 6 years of Python. His last project was a routing engine. 51 years old.\n")
    out_a, _ = redact_protected(a)
    out_b, _ = redact_protected(b)
    assert out_a == out_b
    assert "6 years of Python" in out_a


def test_findings_point_into_the_input_and_hold_no_text():
    out, findings = redact_protected(CV_EN)
    assert findings == sorted(findings, key=lambda f: f.start)
    for f in findings:
        assert [x.name for x in dataclasses.fields(f)] == ["kind", "start", "end", "level"]
        assert 0 <= f.start < f.end <= len(CV_EN)
        assert CV_EN[f.start:f.end] not in repr(f)
    # spans do not overlap
    for prev, cur in zip(findings, findings[1:]):
        assert prev.end <= cur.start


def test_clean_text_is_unchanged():
    text = "Backend engineer, 6 years of Python and PostgreSQL, AWS certified, speaks English and German."
    out, findings = redact_protected(text)
    assert out == text and findings == []


def test_christian_as_a_first_name_survives():
    out, _ = redact_protected("Reference: Christian Hoffmann, team lead")
    assert "Christian Hoffmann" in out


# ---- hidden characters: the screening model reads straight through them -----------------------

ZWSP, SHY = "​", "­"


def _fullwidth(s: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


@pytest.mark.parametrize("snippet,leak,kind", [
    (f"A{ZWSP}ge: 34\nSkills: Go", "34", "age"),
    (f"Age{ZWSP}: 34\nSkills: Go", "34", "age"),
    (f"Nationa{SHY}lity: Turkish\nSkills: Go", "Turkish", "nationality"),
    (_fullwidth("Age") + ": 34\nSkills: Go", "34", "age"),
    (_fullwidth("Gender") + "： female\nSkills: Go", "female", "gender"),
    (f"Date of b{ZWSP}irth: 14.02.1990\nSkills: Go", "1990", "date_of_birth"),
    (f"I am 4{ZWSP}1 years old.", "years old", "age"),
    (_fullwidth("Religion") + ": Catholic\nSkills: Go", "Catholic", "religion"),
])
def test_hidden_or_fullwidth_characters_do_not_hide_attributes(snippet, leak, kind):
    out, findings = redact_protected(snippet)
    assert leak not in out, out
    assert kind in _kinds(findings)
    assert "Skills: Go" in out or "Skills" not in snippet
    # offsets still point into the raw input
    for f in findings:
        assert 0 <= f.start < f.end <= len(snippet)


def test_counterfactual_pair_with_hidden_characters_is_identical():
    a = f"Candidate: Alex Morgan\nA{ZWSP}ge: 29\nSummary: 6 years of Python.\n"
    b = "Candidate: Alex Morgan\n" + _fullwidth("Age") + ": 51\nSummary: 6 years of Python.\n"
    assert redact_protected(a)[0] == redact_protected(b)[0]
