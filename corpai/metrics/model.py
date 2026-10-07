"""Value objects of the metric layer. Plain data: safe to put in graph state (no tokens, no SQL text)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping

from corpai.types import Level

NO_ACCESS = "no permission or not published"
NO_ACCESS_ZH = "无权限或尚未发布"


def dims_key(dims: Mapping[str, Any] | None) -> str:
    """Canonical key of a dims mapping: sorted "k=v&k=v"; '' for the total row."""
    if not dims:
        return ""
    for k, v in dims.items():
        if any(c in f"{k}{v}" for c in "&="):
            raise ValueError("dimension names and values must not contain '&' or '='")
    return "&".join(f"{k}={dims[k]}" for k in sorted(dims))


@dataclass(frozen=True)
class MetricValue:
    """One metric figure as a principal may see it.

    status is draft | signed | published, or "unavailable" when nothing is visible to the caller: that is
    an explicit "no permission or not published", never a silent zero (ENGINEERING.md rule 6).
    """

    metric_id: str
    period: str
    dims: Mapping[str, str]
    value: Decimal | None
    unit: str
    status: str
    level: Level
    suppressed: bool = False
    provenance: Mapping[str, Any] = field(default_factory=dict)  # sql_sha256, computed_at, signed_by, version…
    reason: str | None = None
    name_zh: str = ""
    name_en: str = ""

    @property
    def available(self) -> bool:
        # "computed" = a figure code derived for this report (signal evidence), not a stored snapshot
        return self.status in ("draft", "signed", "published", "computed")

    @property
    def has_value(self) -> bool:
        return self.available and not self.suppressed and self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {"metric_id": self.metric_id, "period": self.period, "dims": dict(self.dims),
                "value": None if self.value is None else str(self.value), "unit": self.unit,
                "status": self.status, "level": int(self.level), "suppressed": self.suppressed,
                "provenance": dict(self.provenance), "reason": self.reason,
                "name_zh": self.name_zh, "name_en": self.name_en}


def unavailable(metric_id: str, period: str, dims: Mapping[str, str] | None, unit: str, *,
                name_zh: str = "", name_en: str = "", reason: str = NO_ACCESS) -> MetricValue:
    return MetricValue(metric_id=metric_id, period=period, dims=dict(dims or {}), value=None, unit=unit,
                       status="unavailable", level=Level.L1, reason=reason, name_zh=name_zh, name_en=name_en)


@dataclass(frozen=True)
class SignResult:
    period: str
    changed: tuple[str, ...]      # metric ids with at least one row moved forward
    rows: int
    skipped: tuple[str, ...]      # requested ids with nothing in the required state

    def to_dict(self) -> dict[str, Any]:
        return {"period": self.period, "changed": list(self.changed), "rows": self.rows,
                "skipped": list(self.skipped)}


@dataclass(frozen=True)
class RefreshReport:
    period: str
    metrics: int
    rows: int
    inserted: int
    updated: int
    unchanged: int
    suppressed: int
    new_versions: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)
