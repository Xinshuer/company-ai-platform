"""Deterministic reading of public company pages: the FakeLLM's extractor and the backtest's fit input.

Pure functions over page dicts (`url, domain, title, text, published_on, kind`). They use only the
page text plus small fixed vocabularies (industries, legal suffixes, city names of FleetPilot's
markets), so the fake model "reads" the same evidence a real one would and nothing else.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from corpai.sales.profile import band_employees, band_fleet
from corpai.sales.untrusted import strip_markup

_INDUSTRY_WORDS: tuple[tuple[str, str], ...] = (
    # longest phrases first
    ("cold-chain food distribution", "cold_chain_food_distribution"),
    ("building supplies distribution", "building_supplies_distribution"),
    ("construction logistics", "construction_logistics"),
    ("beverage distribution", "beverage_distribution"),
    ("refrigerated transport", "refrigerated_transport"),
    ("retail distribution", "retail_distribution"),
    ("freight forwarding", "freight_forwarding"),
    ("parcel delivery", "parcel_delivery"),
    ("express courier", "express_courier"),
    ("regional haulage", "regional_haulage"),
    ("logistics", "other_logistics"),
)

_SUFFIX_COUNTRY = (("Sp. z o.o.", "PL"), ("S.A.S.", "FR"), ("S.r.l.", "IT"), ("B.V.", "NL"), ("S.L.", "ES"))

# Cities in FleetPilot's markets that appear in company pages ("based in <city>").
CITY_COUNTRY: dict[str, str] = {
    **dict.fromkeys(["Hamburg", "Dresden", "Bremen", "Hannover", "Leipzig", "Dortmund", "Nuremberg", "Kassel",
                     "Kiel", "Berlin", "Munich", "Cologne", "Frankfurt", "Stuttgart"], "DE"),
    **dict.fromkeys(["Linz", "Graz", "Salzburg", "Vienna", "Klagenfurt", "Innsbruck"], "AT"),
    **dict.fromkeys(["Rotterdam", "Eindhoven", "Tilburg", "Groningen", "Venlo", "Amsterdam", "Utrecht"], "NL"),
    **dict.fromkeys(["Lyon", "Lille", "Nantes", "Toulouse", "Strasbourg", "Rennes", "Paris", "Marseille"], "FR"),
    **dict.fromkeys(["Poznan", "Wroclaw", "Lodz", "Gdansk", "Katowice", "Warsaw", "Krakow"], "PL"),
    **dict.fromkeys(["Bologna", "Verona", "Turin", "Padua", "Bari", "Milan", "Rome", "Naples"], "IT"),
    **dict.fromkeys(["Valencia", "Zaragoza", "Seville", "Bilbao", "Valladolid", "Madrid", "Barcelona"], "ES"),
}

_GROWTH: tuple[tuple[str, str], ...] = (
    (r"\bopens? (?:a )?new (?:cross-dock|hub|depot|warehouse|site)", "new_site"),
    (r"\btenders? for\b|\bstarted a tender\b", "software_tender"),
    (r"\b(?:new vehicles|fleet expansion|expands? (?:its|the) fleet|modernise its fleet)\b", "fleet_expansion"),
    (r"\btrials? electric vans\b|\belectric vans join\b", "ev_trial"),
    (r"\bhiring (?:drivers|dispatchers)\b|\bdriver shortage\b", "hiring"),
    (r"\bwins? (?:a |the )?(?:regional |multi-year )?[\w -]{0,30}contract\b", "new_contract"),
    (r"\braises?\b[^.\n]{0,40}\bEUR\b|\bhas raised\b", "funding_round"),
)
_TECH: tuple[tuple[str, str], ...] = (
    (r"\b(?:route optimisation|planning) software\b|\bplanning tools?\b", "evaluating_planning_software"),
    (r"\belectric vans?\b|\brange-aware\b", "electric_fleet"),
    (r"\btelematics\b|\btelemetry\b", "telematics"),
    (r"\bspreadsheets?\b", "legacy_planning"),
    (r"\bmulti-depot\b|\bfrom \d+ depots\b", "multi_depot"),
)
_EMPLOYEES = re.compile(r"\bAbout ([\d,]{1,7}) employees\b", re.IGNORECASE)
_FLEET = re.compile(r"\boperate ([\d,]{1,7}) vehicles\b|\bcovering its ([\d,]{1,7}) vehicles\b", re.IGNORECASE)
_RAISED = re.compile(r"\braised EUR ([\d,]{1,15})\b", re.IGNORECASE)
_BASED = re.compile(r"\bbased in ([A-Z][A-Za-z-]{2,30})\b")
_COMPANY_LINE = re.compile(r"^(?P<name>[A-Z][\w .,&'-]{1,100}?) is an? [a-z -]{3,60} company\b", re.MULTILINE)


def _int(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s.replace(",", ""))
    except ValueError:
        return None


def clean(page: dict) -> dict:
    """The page as the model sees it: markup and HTML comments removed (they are not published text)."""
    return {**page, "text": strip_markup(str(page.get("text") or "")),
            "title": strip_markup(str(page.get("title") or ""))}


def extract_signals(pages: Iterable[dict]) -> dict[str, Any]:
    """A raw profile dict (CompanyProfile field names) from page text. Unknown facts are left out."""
    pages = [clean(p) for p in pages]
    text = "\n".join(f"{p['title']}\n{p['text']}" for p in pages)
    out: dict[str, Any] = {}

    site = next((p for p in pages if p.get("kind") == "site"), None)
    m = _COMPANY_LINE.search(site["text"]) if site else None
    if m:
        out["company"] = m.group("name").strip()
        out["domain"] = site.get("domain")

    lowered = text.lower()
    for phrase, industry in _INDUSTRY_WORDS:
        if f"is a {phrase} company" in lowered or f"is an {phrase} company" in lowered:
            out["industry"] = industry
            break

    country = None
    name = out.get("company", "")
    for suffix, cc in _SUFFIX_COUNTRY:
        if name.endswith(suffix):
            country = cc
    city = _BASED.search(text)
    if city and city.group(1) in CITY_COUNTRY:
        country = country or CITY_COUNTRY[city.group(1)]
        if name.endswith("GmbH"):
            country = CITY_COUNTRY[city.group(1)]  # GmbH is both German and Austrian
    if country:
        out["hq_country"] = country
        out["countries"] = [country]

    fleet = next((_int(a or b) for a, b in _FLEET.findall(text) if _int(a or b)), None)
    if fleet:
        out["fleet_size_band"] = band_fleet(fleet).value
    emp = _EMPLOYEES.search(text)
    if emp and _int(emp.group(1)):
        out["employees_band"] = band_employees(_int(emp.group(1))).value

    raised = _RAISED.search(text)
    if raised and _int(raised.group(1)):
        out["funding_stage"] = "growth" if "growth fund" in lowered else "venture"
        out["last_funding_eur"] = _int(raised.group(1))
    elif "family-owned" in lowered or "no outside investors" in lowered:
        out["funding_stage"] = "bootstrapped"

    out["growth_signals"] = sorted({sig for pat, sig in _GROWTH if re.search(pat, text, re.IGNORECASE)})
    out["tech_signals"] = sorted({sig for pat, sig in _TECH if re.search(pat, text, re.IGNORECASE)})
    out["sources"] = [p["url"] for p in pages if p.get("url")]
    return out
