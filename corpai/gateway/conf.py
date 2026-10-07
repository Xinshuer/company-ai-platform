"""config/models.yaml parsed into typed, validated settings.

A malformed file raises at load time: a gateway that guesses its limits could, for example, run
without a concurrency cap or with retries that hammer the API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from corpai.config import load_yaml
from corpai.types import Route


@dataclass(frozen=True)
class RouteConfig:
    name: Route
    base_url: str
    model: str
    api_key_env: str  # the variable NAME; the value is read at call time and never stored
    max_concurrency: int
    queue_timeout_s: float
    timeout_s: float
    egress: str  # destination in config/egress.yaml
    simulated_onprem: bool = False


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0

    def delay(self, attempt: int) -> float:
        """Backoff before attempt `attempt + 1` (attempt counts from 1)."""
        return min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))


@dataclass(frozen=True)
class GatewayConfig:
    routes: Mapping[Route, RouteConfig]
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    retry: RetryConfig = field(default_factory=RetryConfig)
    max_tokens_cap: int = 4096

    def route(self, r: Route) -> RouteConfig:
        return self.routes[r]


def _pos_num(d: Mapping, key: str, where: str, *, integer: bool = False) -> Any:
    v = d.get(key)
    ok = isinstance(v, int) if integer else isinstance(v, (int, float))
    if not ok or isinstance(v, bool) or v <= 0:
        raise ValueError(f"models.yaml: {where}.{key} must be a positive {'integer' if integer else 'number'}")
    return v


def _str(d: Mapping, key: str, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise ValueError(f"models.yaml: {where}.{key} must be a non-empty string")
    return v


def parse_config(raw: Mapping[str, Any]) -> GatewayConfig:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("routes"), Mapping):
        raise ValueError("models.yaml: missing routes")
    routes: dict[Route, RouteConfig] = {}
    for r in Route:
        d = raw["routes"].get(str(r))
        if not isinstance(d, Mapping):
            raise ValueError(f"models.yaml: route {r} is missing")
        where = f"routes.{r}"
        routes[r] = RouteConfig(
            name=r,
            base_url=_str(d, "base_url", where),
            model=_str(d, "model", where),
            api_key_env=_str(d, "api_key_env", where),
            max_concurrency=_pos_num(d, "max_concurrency", where, integer=True),
            queue_timeout_s=float(_pos_num(d, "queue_timeout_s", where)),
            timeout_s=float(_pos_num(d, "timeout_s", where)),
            egress=_str(d, "egress", where),
            simulated_onprem=bool(d.get("simulated_onprem", False)),
        )
    extra = raw.get("extra_body") or {}
    if not isinstance(extra, Mapping):
        raise ValueError("models.yaml: extra_body must be a mapping")
    rd = raw.get("retry") or {}
    retry = RetryConfig(
        max_attempts=_pos_num(rd, "max_attempts", "retry", integer=True) if "max_attempts" in rd else 3,
        base_delay_s=float(_pos_num(rd, "base_delay_s", "retry")) if "base_delay_s" in rd else 0.5,
        max_delay_s=float(_pos_num(rd, "max_delay_s", "retry")) if "max_delay_s" in rd else 8.0,
    )
    cap = raw.get("max_tokens_cap", 4096)
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        raise ValueError("models.yaml: max_tokens_cap must be a positive integer")
    return GatewayConfig(routes=routes, extra_body=dict(extra), retry=retry, max_tokens_cap=cap)


def load_config() -> GatewayConfig:
    return parse_config(load_yaml("models"))
