"""The sales follow-up playbook (corpai/sales/playbook.yaml): rules as data, selected by tier.

Every output that used it records `playbook_id@version` (phase-B spec), and its level (L3) must be
merged into the session's high-water mark before any model call reads it.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from corpai.types import Level

PATH = Path(__file__).with_name("playbook.yaml")
TIERS = ("high", "medium", "low")
_TIER_KEYS = {"goal", "cadence_days", "max_touches_per_month", "channels", "next_steps", "allowed_commitments",
              "needs_approval", "forbidden_commitments"}


class PlaybookError(ValueError):
    pass


@dataclass(frozen=True)
class PlaybookSelection:
    playbook_id: str
    version: int
    ref: str  # "<id>@<version>"
    level: Level
    owner_group: str
    tier: str
    goal: str
    cadence_days: int
    max_touches_per_month: int
    channels: tuple[str, ...]
    next_steps: tuple[str, ...]
    allowed_commitments: tuple[str, ...]
    needs_approval: tuple[str, ...]
    forbidden_commitments: tuple[str, ...]


def _validate(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise PlaybookError("playbook must be a mapping")
    for k in ("id", "version", "status", "level", "owner_group", "tiers", "dept"):
        if k not in raw:
            raise PlaybookError(f"playbook misses {k!r}")
    if raw["status"] != "active":
        raise PlaybookError("only an active playbook may be used")
    if raw["dept"] != "sales" or raw["owner_group"] != "head:sales":
        raise PlaybookError("the sales playbook is owned by the head of sales")
    if set(raw["tiers"]) != set(TIERS):
        raise PlaybookError("playbook must define exactly the tiers high, medium, low")
    for t, body in raw["tiers"].items():
        if set(body) != _TIER_KEYS:
            raise PlaybookError(f"tier {t}: keys must be {sorted(_TIER_KEYS)}")
        if not (isinstance(body["cadence_days"], int) and body["cadence_days"] > 0):
            raise PlaybookError(f"tier {t}: cadence_days must be a positive integer")
    return raw


@functools.cache
def load_playbook(path: Path = PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return _validate(yaml.safe_load(f))


def select_playbook(tier: str, *, path: Path = PATH) -> PlaybookSelection:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    pb = load_playbook(path)
    body = pb["tiers"][tier]
    level = Level[str(pb["level"])]
    return PlaybookSelection(
        playbook_id=pb["id"], version=int(pb["version"]), ref=f"{pb['id']}@{pb['version']}", level=level,
        owner_group=pb["owner_group"], tier=tier, goal=body["goal"], cadence_days=body["cadence_days"],
        max_touches_per_month=body["max_touches_per_month"], channels=tuple(body["channels"]),
        next_steps=tuple(body["next_steps"]), allowed_commitments=tuple(body["allowed_commitments"]),
        needs_approval=tuple(body["needs_approval"]), forbidden_commitments=tuple(body["forbidden_commitments"]))
