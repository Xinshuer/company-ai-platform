"""Everything is fictional: every email and domain ends in .example, company names carry legal suffixes,
and docs/company.md is the rendered script with its disclaimer."""

from __future__ import annotations

import re

import pytest

from corpai.config import ROOT, settings
from corpai.db import pg as pgmod
from corpai.synth import script
from corpai.synth.writer import TABLES

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")
URL = re.compile(r"https?://([A-Za-z0-9.-]+)")
# a bare host name with a real TLD, not part of a longer token such as an email local part (jan.de.vries@...)
DOMAINISH = re.compile(r"(?<![\w.@-])((?:[a-z0-9-]+\.)+(?:com|de|nl|fr|pl|at|it|es|eu|net|org|io|co))(?![\w.@-])")
SUFFIXES = ("GmbH", "B.V.", "S.A.S.", "Sp. z o.o.", "S.r.l.", "S.L.")


def _domains(text: str) -> set[str]:
    return {m.rstrip(".") for m in EMAIL.findall(text)} | {m.rstrip(".") for m in URL.findall(text)}


@pytest.fixture(scope="module")
def all_text(synth_small) -> str:
    parts = []
    with pgmod.connect_owner() as c:
        for t in TABLES:
            parts += [r["x"] for r in c.execute(f"SELECT x::text AS x FROM {t} x").fetchall()]
    root = settings().corpus_dir
    parts += [p.read_text(encoding="utf-8") for p in sorted(root.rglob("*")) if p.is_file()]
    return "\n".join(parts)


def test_every_email_and_url_host_ends_in_example(all_text):
    domains = _domains(all_text)
    assert domains, "expected emails and urls in the generated data"
    bad = sorted(d for d in domains if not d.lower().endswith(".example"))
    assert bad == []


def test_no_real_looking_domains(all_text):
    assert DOMAINISH.findall(all_text.lower()) == []


def test_columns_holding_domains(synth_small):
    with pgmod.connect_owner() as c:
        assert c.execute("SELECT count(*) AS n FROM crm.accounts WHERE domain NOT LIKE '%.example'").fetchone()["n"] == 0
        for t, col in (("core.users", "email"), ("crm.contacts", "email"), ("hr.candidates", "email")):
            n = c.execute(f"SELECT count(*) AS n FROM {t} WHERE {col} NOT LIKE '%%.example'").fetchone()["n"]
            assert n == 0, t
        staff = c.execute("SELECT count(*) AS n FROM core.users WHERE email NOT LIKE '%%@kestrelbyte.example'"
                          ).fetchone()["n"]
        assert staff == 0


def test_company_names_are_invented_compounds_with_legal_suffix(synth_small):
    with pgmod.connect_owner() as c:
        names = [r["name"] for r in c.execute("SELECT name FROM crm.accounts").fetchall()]
    real = re.compile(r"\b(DHL|Schenker|Kuehne|Nagel|Amazon|Google|Microsoft|SAP|Siemens|Bosch|UPS|FedEx|DPD|GLS|"
                      r"Hermes|Rhenus|Dachser|Maersk|Geodis|Raben|Girteka|Transporeon|Trimble)\b", re.I)
    for n in names:
        assert n.endswith(SUFFIXES), n
        assert not real.search(n), n


def test_company_md_is_the_rendered_script():
    text = (ROOT / "docs" / "company.md").read_text(encoding="utf-8")
    assert text == script.render_markdown(), "run: python -m corpai synth --write-company-doc"
    assert "fictional" in text.lower() and ".example" in text
    for p in ("Kestrelbyte GmbH", "FleetPilot", "Berlin"):
        assert p in text
    assert "\r\n" not in text


def test_phone_numbers_are_fictional_555_numbers(synth_small):
    with pgmod.connect_owner() as c:
        for t in ("crm.contacts", "hr.candidates"):
            phones = [r["phone"] for r in c.execute(f"SELECT phone FROM {t}").fetchall()]
            assert phones and all(re.fullmatch(r"\+\d{2} 555 \d{7}", p) for p in phones), t
