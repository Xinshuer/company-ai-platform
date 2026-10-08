"""Résumé preparation and the quote check (pure code, no database).

Review findings covered here: name removal was case-sensitive and missed handles/URLs and
"A. Surname"; employment ranges and large numbers were removed as phone numbers; a lone name token
replaced ordinary words (a city equal to the surname); a single character or a placeholder counted
as a verbatim quote for a "met" verdict.
"""

from __future__ import annotations

import re

import pytest

from corpai.hr import Verdict, prepare_resume
from corpai.hr.evidence import check_quote

NAME = "Anna Kowalczyk"


def _no_name(text: str, name: str = NAME) -> None:
    folded = text.casefold()
    for token in name.casefold().split():
        assert not re.search(rf"(?<!\w){re.escape(token)}", folded), (token, text)
    assert "annakowalczyk" not in folded


@pytest.mark.parametrize("resume", [
    "ANNA KOWALCZYK\nBackend engineer\n\n## Profile\nBackend engineer.\n",
    "anna kowalczyk\nBackend engineer\n",
    "Kowalczyk, Anna\nBackend engineer\n",
    "A. Kowalczyk\nBackend engineer\n",
    "Backend engineer\n\n## Links\nProfile: anna.kowalczyk on GitHub (github.com/annakowalczyk)\n",
    "Backend engineer\n\n## Links\nhttps://www.linkedin.com/in/anna-kowalczyk-123 and @akowalczyk\n",
])
def test_name_is_removed_in_any_case_and_in_handles(resume):
    _no_name(prepare_resume(resume, name=NAME).text)


def test_diacritics_do_not_hide_a_name():
    text = prepare_resume("ZOE MULLER\nPlanner\n\nZoë Müller led the depot team.\n", name="Zoë Müller").text
    assert "zoe" not in text.casefold() and "muller" not in text.casefold() and "müller" not in text.casefold()


def test_counterfactual_pair_with_uppercase_header_and_profile_url_gets_identical_input():
    body = ("\nBackend engineer\n\n## Experience\n- 03/2019 - 06/2023  Backend engineer at Kranich Software GmbH, "
            "Berlin: handled 40 000 000 stops per year.\n\n## Links\ngithub.com/{handle}\n")
    a = prepare_resume("ANNA KOWALCZYK" + body.format(handle="annakowalczyk"), name="Anna Kowalczyk")
    b = prepare_resume("JONAS BERLIN" + body.format(handle="jonasberlin"), name="Jonas Berlin")
    assert a.text == b.text


# issues-log FN-27: the same résumé in Swedish, differing in name, gender, age, family and orientation
SV_PAIR = (
    ("Maja Ekberg", "Maja Ekberg\nKön: kvinna | Födelsedatum: 1996-05-14 | Ålder: 29 år\n"
                      "Civilstånd: sambo, ett barn\nSexuell läggning: lesbisk\n\n## Erfarenhet\n"
                      "2019-2025 Backendutvecklare på Kranich Software AB, Stockholm: Python, Kafka.\n"
                      "Hon införde kontraktstester. Hennes team hanterade 40 000 000 stopp per år.\n"),
    ("Nils Ahlgren", "Nils Ahlgren\nKön: man | Födelsedatum: 1974-11-02 | Ålder: 51 år\n"
                       "Civilstånd: gift, tre barn\nSexuell läggning: heterosexuell\n\n## Erfarenhet\n"
                       "2019-2025 Backendutvecklare på Kranich Software AB, Stockholm: Python, Kafka.\n"
                       "Han införde kontraktstester. Hans team hanterade 40 000 000 stopp per år.\n"),
)


def test_swedish_counterfactual_pair_gets_identical_input():
    (na, ra), (nb, rb) = SV_PAIR
    a, b = prepare_resume(ra, name=na), prepare_resume(rb, name=nb)
    assert a.text == b.text
    assert "Python, Kafka" in a.text and "40 000 000 stopp per år" in a.text
    assert {"gender", "date_of_birth", "age", "marital_status", "sexual_orientation"} <= set(a.removed_kinds)


def test_employment_ranges_and_large_numbers_are_not_phone_numbers():
    resume = ("Anna Berlin\nBackend engineer\n\n## Experience\n"
              "03/2019 - 06/2023  Backend engineer, Kranich Software GmbH, Berlin\n"
              "Handled 40 000 000 stops per year; 120 000 000 events a day.\n"
              "Phone: 030 1234 5678 | mobile +49 151 2345 6789\n")
    text = prepare_resume(resume, name="Anna Berlin").text
    assert "03/2019 - 06/2023" in text
    assert "40 000 000 stops" in text and "120 000 000 events" in text
    assert "Kranich Software GmbH, Berlin" in text  # a city equal to the surname is not the candidate
    assert "1234 5678" not in text and "2345 6789" not in text and text.count("⟨removed:phone⟩") == 2
    assert text.startswith("the candidate\n")


@pytest.mark.parametrize("quote", ["e", "the candidate", "⟨removed:email⟩", "Go, Kafka", "⟨removed:email⟩ ⟨removed:phone⟩"])
def test_trivial_or_placeholder_quotes_do_not_support_a_verdict(quote):
    resume = "the candidate\n⟨removed:email⟩ ⟨removed:phone⟩\n\n## Skills\nGo, Kafka, Kubernetes, PostgreSQL\n"
    assert quote in resume
    for verdict in (Verdict.MET, Verdict.NOT_MET):
        v, q, flags = check_quote(verdict, quote, resume, [])
        assert (v, q) == (Verdict.UNKNOWN, None) and flags == ["quote_too_short"], quote


def test_a_substantive_quote_still_counts():
    resume = "the candidate\n\n## Skills\nGo, Kafka, Kubernetes, PostgreSQL\n"
    assert check_quote(Verdict.MET, "Go, Kafka, Kubernetes", resume, []) == (Verdict.MET, "Go, Kafka, Kubernetes", [])
