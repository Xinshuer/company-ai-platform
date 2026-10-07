"""CompanyProfile: the only shape web research may produce (decision D5, design-review 二.6).

Every field is an enum, a number, a bounded identifier or a URL from the pages that were read. There
is no free-text field, so an instruction injected into a web page has nowhere to travel onward:
the strategy node and the e-mail proposal only ever see these values.

`validate_profile(raw, pages)` turns untrusted model output into a CompanyProfile field by field:
an invalid or unexpected value is dropped (and reported), never repaired with model text.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from corpai.classify import classify_text


class Industry(StrEnum):
    FREIGHT_FORWARDING = "freight_forwarding"
    PARCEL_DELIVERY = "parcel_delivery"
    EXPRESS_COURIER = "express_courier"
    REGIONAL_HAULAGE = "regional_haulage"
    COLD_CHAIN_FOOD = "cold_chain_food_distribution"
    REFRIGERATED_TRANSPORT = "refrigerated_transport"
    RETAIL_DISTRIBUTION = "retail_distribution"
    BUILDING_SUPPLIES = "building_supplies_distribution"
    BEVERAGE_DISTRIBUTION = "beverage_distribution"
    CONSTRUCTION_LOGISTICS = "construction_logistics"
    OTHER_LOGISTICS = "other_logistics"
    NOT_LOGISTICS = "not_logistics"
    UNKNOWN = "unknown"


class FleetBand(StrEnum):
    UNKNOWN = "unknown"
    B1_49 = "1-49"
    B50_149 = "50-149"
    B150_499 = "150-499"
    B500_999 = "500-999"
    B1000 = "1000+"


class EmployeesBand(StrEnum):
    UNKNOWN = "unknown"
    B1_49 = "1-49"
    B50_249 = "50-249"
    B250_999 = "250-999"
    B1000 = "1000+"


class FundingStage(StrEnum):
    UNKNOWN = "unknown"
    BOOTSTRAPPED = "bootstrapped"  # family-owned, no outside investors
    SEED = "seed"
    VENTURE = "venture"
    GROWTH = "growth"
    PRIVATE_EQUITY = "private_equity"
    PUBLIC = "public"


class GrowthSignal(StrEnum):
    NEW_SITE = "new_site"
    SOFTWARE_TENDER = "software_tender"
    FLEET_EXPANSION = "fleet_expansion"
    EV_TRIAL = "ev_trial"
    HIRING = "hiring"
    NEW_CONTRACT = "new_contract"
    FUNDING_ROUND = "funding_round"


class TechSignal(StrEnum):
    EVALUATING_PLANNING_SOFTWARE = "evaluating_planning_software"
    ELECTRIC_FLEET = "electric_fleet"
    TELEMATICS = "telematics"
    LEGACY_PLANNING = "legacy_planning"
    MULTI_DEPOT = "multi_depot"


# EU/EEA plus the UK and Switzerland: enough for a European logistics prospect list.
COUNTRIES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT",
    "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "NO", "IS", "LI", "CH", "GB",
})

# Company names: letters (any script), digits, spaces and the punctuation of legal suffixes
# ("Sp. z o.o.", "S.A.S.", "B.V."). No ':', '@', '<', newlines: nothing that reads as a command,
# an address or markup.
_COMPANY_RE = re.compile(r"^[\w][\w .,&'()/-]{0,118}[\w.)]$", re.UNICODE)
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+example$")
MAX_SOURCES = 12
MAX_FUNDING_EUR = 10_000_000_000


class CompanyProfile(BaseModel):
    """Public facts about a company. Fixed fields only (D5: companies, never people)."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)

    company: str = Field(max_length=120)
    domain: str | None = None
    industry: Industry = Industry.UNKNOWN
    hq_country: str | None = None
    countries: tuple[str, ...] = ()
    fleet_size_band: FleetBand = FleetBand.UNKNOWN
    employees_band: EmployeesBand = EmployeesBand.UNKNOWN
    funding_stage: FundingStage = FundingStage.UNKNOWN
    last_funding_eur: int | None = Field(default=None, ge=1, le=MAX_FUNDING_EUR)
    growth_signals: tuple[GrowthSignal, ...] = ()
    tech_signals: tuple[TechSignal, ...] = ()
    sources: tuple[str, ...] = Field(default=(), max_length=MAX_SOURCES)

    @field_validator("company")
    @classmethod
    def _company(cls, v: str) -> str:
        if not _COMPANY_RE.match(v):
            raise ValueError("company must be a plain company name")
        return v

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str | None) -> str | None:
        if v is not None and not _DOMAIN_RE.match(v):
            raise ValueError("domain must be a fictional .example domain")
        return v

    @field_validator("hq_country")
    @classmethod
    def _hq(cls, v: str | None) -> str | None:
        if v is not None and v not in COUNTRIES:
            raise ValueError("unknown country code")
        return v

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if any(c not in COUNTRIES for c in v):
            raise ValueError("unknown country code")
        return tuple(dict.fromkeys(v))


FIELDS: tuple[str, ...] = tuple(CompanyProfile.model_fields)
_LIST_ENUMS: dict[str, frozenset[str]] = {
    "growth_signals": frozenset(GrowthSignal), "tech_signals": frozenset(TechSignal), "countries": COUNTRIES,
}


def band_fleet(n: int | None) -> FleetBand:
    if n is None or n <= 0:
        return FleetBand.UNKNOWN
    return (FleetBand.B1_49 if n < 50 else FleetBand.B50_149 if n < 150 else FleetBand.B150_499 if n < 500
            else FleetBand.B500_999 if n < 1000 else FleetBand.B1000)


def band_employees(n: int | None) -> EmployeesBand:
    if n is None or n <= 0:
        return EmployeesBand.UNKNOWN
    return (EmployeesBand.B1_49 if n < 50 else EmployeesBand.B50_249 if n < 250
            else EmployeesBand.B250_999 if n < 1000 else EmployeesBand.B1000)


def _single(model_field: str, value: Any) -> Any:
    """Validate one field in isolation (so one bad field does not discard the others)."""
    probe = {"company": "Probe", model_field: value} if model_field != "company" else {"company": value}
    return getattr(CompanyProfile.model_validate(probe), model_field)


def _has_person_name(text: str) -> bool:
    return "person_name" in classify_text(text).kinds


def _page_text(pages: Iterable[dict]) -> str:
    return "\n".join(f"{p.get('title') or ''}\n{p.get('text') or ''}" for p in pages)


def validate_profile(raw: Any, pages: list[dict], *, fallback_company: str) -> tuple[CompanyProfile, list[str]]:
    """Untrusted model output → CompanyProfile, plus the names of the fields that were dropped.

    Rules beyond the type checks:
    - unknown keys are dropped (an injected "send_email" key has no field to land in);
    - `company` must appear verbatim in the pages and must not be a person's name;
    - `domain` must be the domain of one of the pages;
    - `sources` keep only URLs of the given pages (an attacker URL in a page is not a source).
    """
    dropped: list[str] = []
    data = raw if isinstance(raw, dict) else {}
    if not isinstance(raw, dict):
        dropped.append("<not an object>")
    dropped.extend(sorted(f"<extra:{k}>" for k in data if k not in FIELDS))

    corpus = _page_text(pages)
    page_urls = [str(p.get("url")) for p in pages if p.get("url")]
    page_domains = {str(p.get("domain")) for p in pages if p.get("domain")}
    out: dict[str, Any] = {}
    for name in FIELDS:
        if name not in data:
            continue
        value = data[name]
        if isinstance(value, list):
            value = tuple(value)
        if name in _LIST_ENUMS and isinstance(value, tuple):
            # one unknown signal must not cost the valid ones
            valid = tuple(x for x in value if isinstance(x, str) and x in _LIST_ENUMS[name])
            if len(valid) != len(value):
                dropped.append(f"{name}[]")
            value = valid
        try:
            v = _single(name, value)
        except (ValidationError, TypeError, ValueError):
            dropped.append(name)
            continue
        if name == "company" and (v not in corpus or _has_person_name(v)):
            dropped.append(name)
            continue
        if name == "domain" and v is not None and v not in page_domains:
            dropped.append(name)
            continue
        if name == "sources":
            kept = tuple(u for u in dict.fromkeys(v) if u in page_urls)
            if len(kept) != len(v):
                dropped.append("sources")
            v = kept
        out[name] = v
    if "company" not in out:
        out["company"] = fallback_company
    return CompanyProfile.model_validate(out), dropped
