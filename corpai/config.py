"""Settings from environment variables plus YAML files in config/.

`settings()` is cached; tests that change environment variables call `reload_settings()`.
API keys are never stored here: the gateway reads them from the environment at call time.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_mode: str  # "fake" | "live"
    embed_mode: str  # "fake" | "fastembed"
    demo_login: bool
    api_base: str
    sim_today: date

    @property
    def pg_dir(self) -> Path:
        return self.data_dir / "pg"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def corpus_dir(self) -> Path:
        return self.data_dir / "corpus"

    @property
    def cache_dir(self) -> Path:
        return ROOT / ".cache"


def _load() -> Settings:
    data_dir = Path(os.environ.get("CORPAI_DATA_DIR", ".data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    llm = os.environ.get("CORPAI_LLM", "fake")
    embed = os.environ.get("CORPAI_EMBED", "fake")
    if llm not in ("fake", "live"):
        raise ValueError(f"CORPAI_LLM must be fake or live, got {llm!r}")
    if embed not in ("fake", "fastembed"):
        raise ValueError(f"CORPAI_EMBED must be fake or fastembed, got {embed!r}")
    return Settings(
        data_dir=data_dir,
        llm_mode=llm,
        embed_mode=embed,
        demo_login=os.environ.get("CORPAI_DEMO_LOGIN", "1") == "1",
        api_base=os.environ.get("CORPAI_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
        sim_today=date.fromisoformat(os.environ.get("CORPAI_SIM_TODAY", "2026-10-01")),
    )


@functools.cache
def settings() -> Settings:
    return _load()


@functools.cache
def load_yaml(name: str) -> Any:
    """config/<name>.yaml, parsed once per process."""
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def reload_settings() -> Settings:
    settings.cache_clear()
    load_yaml.cache_clear()
    return settings()
