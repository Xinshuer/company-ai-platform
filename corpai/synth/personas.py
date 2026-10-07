"""Fixed personas for tests and the demo login. Ids and names never change across seeds.

PERSONAS maps the persona key (also stored in core.users.persona_key) to the user id.
"""

from __future__ import annotations

from dataclasses import dataclass

PERSONAS: dict[str, str] = {
    "ceo": "u0001",
    "exec_staff": "u0002",
    "sales_head": "u0010",
    "sales_staff": "u0011",
    "eng_head": "u0020",
    "eng_staff": "u0021",
    "legal_head": "u0030",
    "legal_staff": "u0031",
    "finance_head": "u0040",
    "finance_staff": "u0041",
    "hr_head": "u0050",
    "hr_staff": "u0051",
    "platform_admin": "u0060",
    "auditor": "u0061",
}


@dataclass(frozen=True)
class PersonaSpec:
    key: str
    user_id: str
    name: str
    dept: str
    role: str
    title: str
    manager_id: str | None
    country: str
    hired_on: str


# Every name is invented. Managers come before their reports (core.users.manager_id is a FK).
# country is the work location: SE = the Stockholm head office.
PERSONA_SPECS: tuple[PersonaSpec, ...] = (
    PersonaSpec("ceo", "u0001", "Helena Lindberg", "exec", "ceo", "Chief Executive Officer", None, "SE", "2019-03-01"),
    PersonaSpec("exec_staff", "u0002", "Johan Ekström", "exec", "employee", "Chief of Staff", "u0001", "SE", "2022-06-01"),
    PersonaSpec("sales_head", "u0010", "Karin Holm", "sales", "manager", "Head of Sales", "u0001", "SE", "2020-02-01"),
    PersonaSpec("sales_staff", "u0011", "Erik Lindqvist", "sales", "employee", "Account Executive", "u0010", "SE", "2023-04-03"),
    PersonaSpec("eng_head", "u0020", "Anders Nyberg", "eng", "manager", "Chief Technology Officer", "u0001", "SE", "2019-03-01"),
    PersonaSpec("eng_staff", "u0021", "Oskar Berg", "eng", "employee", "Backend Engineer", "u0020", "SE", "2022-09-01"),
    PersonaSpec("legal_head", "u0030", "Sofia Wallin", "legal", "manager", "Head of Legal", "u0001", "SE", "2021-01-04"),
    PersonaSpec("legal_staff", "u0031", "Viktor Sandberg", "legal", "employee", "Legal Counsel", "u0030", "SE", "2023-02-01"),
    PersonaSpec("finance_head", "u0040", "Annika Strand", "finance", "manager", "Chief Financial Officer", "u0001", "SE", "2020-09-01"),
    PersonaSpec("finance_staff", "u0041", "Linnea Dahl", "finance", "employee", "Financial Accountant", "u0040", "PL", "2023-08-01"),
    PersonaSpec("hr_head", "u0050", "Maria Engström", "hr", "manager", "Head of People", "u0001", "SE", "2020-05-04"),
    PersonaSpec("hr_staff", "u0051", "Elin Forsberg", "hr", "employee", "Talent Acquisition Partner", "u0050", "SE", "2024-01-08"),
    PersonaSpec("platform_admin", "u0060", "Daniel Sjögren", "it", "platform_admin", "Platform Administrator", "u0001", "SE", "2021-07-01"),
    PersonaSpec("auditor", "u0061", "Ida Lund", "it", "security_auditor", "Security Auditor", "u0001", "NL", "2023-11-01"),
)

SERVICE_ACCOUNTS: tuple[str, ...] = ("svc:ingest", "svc:metrics", "svc:eval", "svc:executor")
