"""The report's sections: which metrics and which issue detectors belong to which domain.

A metric belongs to the domain of its owner (`owner_role: head:<dept>` in config/metrics.yaml), so a
metric added to the YAML lands in a section without touching this file; a metric whose owner is not
one of the five domains fails loudly (tests/m3 checks every metric maps). Signals are listed by hand:
they are code (corpai.metrics.signals), not configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

from corpai.metrics import SIGNAL_KINDS, MetricDef, load_metrics
from corpai.types import Principal, Role


@dataclass(frozen=True)
class Domain:
    id: str
    title_zh: str
    title_en: str
    signals: tuple[str, ...]


DOMAINS: tuple[Domain, ...] = (
    Domain("finance", "财务", "Finance", ("overdue_receivable", "dept_budget_overrun")),
    Domain("sales", "销售", "Sales", ()),
    Domain("eng", "研发", "Engineering", ("project_testing_delayed",)),
    Domain("hr", "人力资源", "People", ("hiring_over_budget",)),
    Domain("legal", "法务", "Legal", ("contract_expiring", "missing_dpa_clause")),
)
DOMAIN_IDS: tuple[str, ...] = tuple(d.id for d in DOMAINS)
_BY_ID = {d.id: d for d in DOMAINS}


class DomainError(ValueError):
    pass


def domain(domain_id: str) -> Domain:
    return _BY_ID[domain_id]


def domain_of(md: MetricDef) -> str:
    """The section a metric belongs to: its owner's department."""
    owner = str(md.owner_role or "")
    if owner.startswith("head:") and owner[5:] in _BY_ID:
        return owner[5:]
    raise DomainError(f"metric {md.id} has owner {owner!r}, which is not one of the report's domains")


def metrics_of(domain_id: str) -> tuple[str, ...]:
    """Metric ids of a section, in the order of config/metrics.yaml."""
    return tuple(m.id for m in load_metrics().values() if domain_of(m) == domain_id)


def export_domain(p: Principal) -> str:
    """The `domain` an export is checked against (policy report-ceo / report-head-own-domain): the CEO
    exports the company report, a head the report of their own department. The report shows each
    requester only what they may read, so a head's report is a report of their domain plus what
    the rest of the company has published to them."""
    return "company" if p.role is Role.CEO else str(p.dept)


def _check() -> None:
    every = {k for d in DOMAINS for k in d.signals}
    if every != set(SIGNAL_KINDS):
        raise DomainError(f"signals without a section: {sorted(set(SIGNAL_KINDS) - every)}")


_check()
