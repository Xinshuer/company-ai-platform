"""The egress inventory (config/egress.yaml) and the check every outbound sender runs first.

Code cannot contact a host that is not listed: OpenAICompatBackend checks its base_url before it
builds a client, and the HTTP client it builds re-checks the host of every request it sends
(redirects included), so a misconfigured base_url or a redirect to another host is refused
before any payload leaves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from corpai.config import load_yaml
from corpai.gateway.errors import EgressDenied
from corpai.types import Level

KINDS = frozenset({"http", "in_process", "simulated"})


@dataclass(frozen=True)
class Destination:
    name: str
    kind: str
    hosts: frozenset[str]
    max_level: Level | None  # None: model payloads may never go there
    simulated_onprem: bool = False


def _level(v: Any, name: str) -> Level | None:
    if v is None:
        return None
    s = str(v).strip().upper()
    if s.startswith("L") and s[1:].isdigit() and 1 <= int(s[1:]) <= 4:
        return Level(int(s[1:]))
    raise ValueError(f"egress.yaml: {name}.max_level must be L1..L4 or null")


def parse_inventory(raw: Mapping[str, Any]) -> dict[str, Destination]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("destinations"), Mapping):
        raise ValueError("egress.yaml: missing destinations")
    out: dict[str, Destination] = {}
    for name, d in raw["destinations"].items():
        if not isinstance(d, Mapping) or d.get("kind") not in KINDS:
            raise ValueError(f"egress.yaml: {name}.kind must be one of {sorted(KINDS)}")
        hosts = d.get("hosts") or []
        if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
            raise ValueError(f"egress.yaml: {name}.hosts must be a list of host names")
        if d["kind"] == "http" and not hosts:
            raise ValueError(f"egress.yaml: {name} is an http destination without hosts")
        out[str(name)] = Destination(name=str(name), kind=d["kind"], hosts=frozenset(h.lower() for h in hosts),
                                     max_level=_level(d.get("max_level"), str(name)),
                                     simulated_onprem=bool(d.get("simulated_onprem", False)))
    return out


def inventory() -> dict[str, Destination]:
    return parse_inventory(load_yaml("egress"))


def host_of(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        raise EgressDenied("malformed outbound URL") from None
    if parts.scheme != "https" or not parts.hostname:
        # Plain http would put the payload (and the Authorization header) on the wire unencrypted.
        raise EgressDenied("outbound model calls must use https to a listed host")
    return parts.hostname.lower()


def check_egress(destination: str, url: str | None, level: Level | int) -> Destination:
    """Raise EgressDenied unless `destination` is listed, `url`'s host is one of its hosts (http
    destinations) and `level` is within its max_level. Returns the destination."""
    dests = inventory()
    dest = dests.get(destination)
    if dest is None:
        raise EgressDenied(f"egress destination {destination!r} is not in config/egress.yaml")
    try:
        lvl = Level(int(level))
    except (TypeError, ValueError):
        raise EgressDenied("egress level is invalid") from None
    if dest.max_level is None or lvl > dest.max_level:
        raise EgressDenied(f"L{int(lvl)} data may not go to {destination}")
    if dest.kind == "http":
        if url is None or host_of(url) not in dest.hosts:
            raise EgressDenied(f"host is not listed for {destination} in config/egress.yaml")
    return dest


def check_host(destination: str, url: str) -> None:
    """Host-only check for each HTTP request a backend sends (the level was checked before)."""
    dest = inventory().get(destination)
    if dest is None or dest.kind != "http" or host_of(url) not in dest.hosts:
        raise EgressDenied(f"host is not listed for {destination} in config/egress.yaml")
