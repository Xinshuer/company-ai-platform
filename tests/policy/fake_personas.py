"""Fabricated principals for the 14 personas of architecture §15, built with the real
`effective_groups()` from fake directory rows. Policy tests do not need the database; the synth
stream writes the real directory in parallel and an integration test checks the RLS side.

Relationships assumed here (they mirror §5.1 "Memberships"):
- sales_staff (u0004) owns CRM account A-0012; another rep u0015 owns A-0020.
- eng_staff (u0006) is on project P-01; u0016 is on P-02.
- legal_staff (u0008) handles contracts and matter M-01.
- hr_staff (u0012) and the hiring manager eng_head (u0005) are on requisition R-01 (an eng role).
- the platform admin reports to the CTO; the security auditor reports to the CEO.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from corpai.identity.directory import principal_from_row
from corpai.types import Principal

PERSONA_KEYS: tuple[str, ...] = (
    "ceo", "exec_staff", "sales_head", "sales_staff", "eng_head", "eng_staff", "legal_head",
    "legal_staff", "finance_head", "finance_staff", "hr_head", "hr_staff", "platform_admin", "auditor",
)

# key: (user_id, name, dept, role, title, manager_id, clearance, memberships)
_PEOPLE: dict[str, tuple] = {
    "ceo": ("u0001", "Clara Brandt", "exec", "ceo", "Chief Executive Officer", None, 4, []),
    "exec_staff": ("u0002", "Jonas Weber", "exec", "employee", "Chief of Staff", "u0001", 3, []),
    "sales_head": ("u0003", "Mira Kowalski", "sales", "manager", "Head of Sales", "u0001", 4, []),
    "sales_staff": ("u0004", "Tim Becker", "sales", "employee", "Account Executive", "u0003", 3, []),
    "eng_head": ("u0005", "Lena Hoffmann", "eng", "manager", "CTO", "u0001", 4, ["req:R-01"]),
    "eng_staff": ("u0006", "Ali Demir", "eng", "employee", "Backend Engineer", "u0005", 3, ["proj:P-01"]),
    "legal_head": ("u0007", "Sophie Martin", "legal", "manager", "Head of Legal", "u0001", 4, []),
    "legal_staff": ("u0008", "Paul Richter", "legal", "employee", "Legal Counsel", "u0007", 4, ["matter:M-01"]),
    "finance_head": ("u0009", "Eva Schulz", "finance", "manager", "CFO", "u0001", 4, []),
    "finance_staff": ("u0010", "Noah Fischer", "finance", "employee", "Accountant", "u0009", 4, []),
    "hr_head": ("u0011", "Hanna Wolf", "hr", "manager", "Head of People", "u0001", 4, []),
    "hr_staff": ("u0012", "Lukas Meyer", "hr", "employee", "HR Generalist", "u0011", 4, ["req:R-01"]),
    "platform_admin": ("u0013", "Ida Novak", "it", "platform_admin", "Platform Admin", "u0005", 2, []),
    "auditor": ("u0014", "Omar Haddad", "it", "security_auditor", "Security Auditor", "u0001", 2, []),
}
# Non-persona employees referenced by resources (they shape managers' mgr-of groups).
_OTHERS: dict[str, tuple] = {
    "other_rep": ("u0015", "Nina Graf", "sales", "employee", "Account Executive", "u0003", 3, []),
    "other_eng": ("u0016", "Jan Vogel", "eng", "employee", "Frontend Engineer", "u0005", 3, ["proj:P-02"]),
    "other_lawyer": ("u0017", "Lea Krause", "legal", "employee", "Legal Counsel", "u0007", 4, []),
}


def _row(spec: tuple, *, grants: list[str] | None = None, active: bool = True) -> dict[str, Any]:
    user_id, name, dept, role, title, manager_id, clearance, memberships = spec
    everyone = {**_PEOPLE, **_OTHERS}
    reports = sorted(s[0] for s in everyone.values() if s[5] == user_id)
    return {
        "user_id": user_id, "name": name, "email": f"{user_id}@kestrelbyte.example", "dept": dept,
        "role": role, "title": title, "manager_id": manager_id, "clearance": clearance, "active": active,
        "persona_key": None, "memberships": list(memberships), "grants": list(grants or []),
        "direct_reports": reports,
    }


def persona(key: str) -> Principal:
    return principal_from_row(_row(_PEOPLE[key]))


def user_id(key: str) -> str:
    return {**_PEOPLE, **_OTHERS}[key][0]


# Variants used by "extra" cases: same person with a grant, deactivated, or misconfigured.
def _variants() -> dict[str, Principal]:
    v: dict[str, Principal] = {
        "legal_staff+crm_grant": principal_from_row(_row(_PEOPLE["legal_staff"], grants=["grant:crm:A-0012"])),
        "ceo+matter_grant": principal_from_row(_row(_PEOPLE["ceo"], grants=["grant:matter:M-01"])),
        "sales_staff~inactive": principal_from_row(_row(_PEOPLE["sales_staff"], active=False)),
        "sales_head~inactive": principal_from_row(_row(_PEOPLE["sales_head"], active=False)),
        "ceo~inactive": principal_from_row(_row(_PEOPLE["ceo"], active=False)),
        "svc_metrics": principal_from_row({
            "user_id": "svc:metrics", "name": "metrics job", "email": "svc-metrics@kestrelbyte.example",
            "dept": "it", "role": "service", "title": "Service account", "manager_id": None,
            "clearance": 4, "active": True, "memberships": [], "grants": [], "direct_reports": []}),
    }
    # Misconfiguration: an ops role somehow given L4 and every business group. The deny rule must
    # still keep it out of L3+ content (defence in depth).
    pa = persona("platform_admin")
    v["platform_admin!overgranted"] = replace(
        pa, clearance=4, groups=pa.groups | {"head:sales", "head:finance", "head:hr", "role:ceo", "dept:legal"})
    au = persona("auditor")
    v["auditor!overgranted"] = replace(au, clearance=4, groups=au.groups | {"head:hr", "role:ceo"})
    return v


VARIANTS: dict[str, Principal] = _variants()


def principal(key: str) -> Principal:
    """A persona key or a variant key."""
    return VARIANTS[key] if key in VARIANTS else persona(key)


def all_personas() -> dict[str, Principal]:
    return {k: persona(k) for k in PERSONA_KEYS}


def resolve(value: Any) -> Any:
    """Replace "@persona" (whole string or after a "prefix:") with that persona's user_id."""
    if isinstance(value, str):
        if value.startswith("@"):
            return user_id(value[1:])
        if ":@" in value:
            prefix, _, key = value.partition(":@")
            return f"{prefix}:{user_id(key)}"
        return value
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    return value
