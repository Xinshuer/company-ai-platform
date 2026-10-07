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
PERSONA_SPECS: tuple[PersonaSpec, ...] = (
    PersonaSpec("ceo", "u0001", "Katharina Wendel", "exec", "ceo", "Chief Executive Officer", None, "DE", "2019-03-01"),
    PersonaSpec("exec_staff", "u0002", "Jonas Albrecht", "exec", "employee", "Chief of Staff", "u0001", "DE", "2022-06-01"),
    PersonaSpec("sales_head", "u0010", "Mareike Fuchs", "sales", "manager", "Head of Sales", "u0001", "DE", "2020-02-01"),
    PersonaSpec("sales_staff", "u0011", "Tobias Reiner", "sales", "employee", "Account Executive", "u0010", "DE", "2023-04-03"),
    PersonaSpec("eng_head", "u0020", "Lukas Brandt", "eng", "manager", "Chief Technology Officer", "u0001", "DE", "2019-03-01"),
    PersonaSpec("eng_staff", "u0021", "Emre Kaya", "eng", "employee", "Backend Engineer", "u0020", "DE", "2022-09-01"),
    PersonaSpec("legal_head", "u0030", "Sophie Hartmann", "legal", "manager", "Head of Legal", "u0001", "DE", "2021-01-04"),
    PersonaSpec("legal_staff", "u0031", "Felix Neumann", "legal", "employee", "Legal Counsel", "u0030", "DE", "2023-02-01"),
    PersonaSpec("finance_head", "u0040", "Annika Vogt", "finance", "manager", "Chief Financial Officer", "u0001", "DE", "2020-09-01"),
    PersonaSpec("finance_staff", "u0041", "Paulina Nowak", "finance", "employee", "Financial Accountant", "u0040", "PL", "2023-08-01"),
    PersonaSpec("hr_head", "u0050", "Clara Becker", "hr", "manager", "Head of People", "u0001", "DE", "2020-05-04"),
    PersonaSpec("hr_staff", "u0051", "Lea Schröder", "hr", "employee", "Talent Acquisition Partner", "u0050", "DE", "2024-01-08"),
    PersonaSpec("platform_admin", "u0060", "Daniel Okafor", "it", "platform_admin", "Platform Administrator", "u0001", "DE", "2021-07-01"),
    PersonaSpec("auditor", "u0061", "Mira Jansen", "it", "security_auditor", "Security Auditor", "u0001", "NL", "2023-11-01"),
)

SERVICE_ACCOUNTS: tuple[str, ...] = ("svc:ingest", "svc:metrics", "svc:eval", "svc:executor")
