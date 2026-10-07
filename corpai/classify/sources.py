"""Default level per data source (config/sources.yaml). Unknown or unreadable → L4 (fail closed)."""

from __future__ import annotations

import fnmatch
from typing import Any

from corpai.config import load_yaml
from corpai.types import Level


def parse_level(value: Any) -> Level:
    """'L3', 'l3', 3 or Level.L3 → Level.L3. Anything else raises ValueError."""
    if isinstance(value, Level):
        return value
    if isinstance(value, bool):
        raise ValueError(f"not a level: {value!r}")
    if isinstance(value, int):
        return Level(value)
    if isinstance(value, str) and value.strip().upper() in Level.__members__:
        return Level[value.strip().upper()]
    raise ValueError(f"not a level: {value!r}")


def _rules() -> list[tuple[str, Level]]:
    data = load_yaml("sources")  # cached per process
    return [(str(r["pattern"]).lower(), parse_level(r["level"])) for r in data["sources"]]


def source_default(source: str) -> Level:
    """First matching pattern wins; "crm.account:A-0012" is matched as "crm.account"."""
    try:
        name = str(source).split(":", 1)[0].strip().lower()
        if not name:
            return Level.L4
        for pattern, level in _rules():
            if fnmatch.fnmatchcase(name, pattern):
                return level
    except Exception:  # a broken config must not make data look public
        return Level.L4
    return Level.L4
