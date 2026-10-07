"""Request and result types of the gateway (architecture §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from corpai.types import Level, Principal, Route


@dataclass
class ChatRequest:
    messages: list[dict]  # OpenAI format
    principal: Principal
    hwm: Level  # from graph state via the runtime helper, never from model output
    purpose: str  # e.g. "qa.answer", "legal.review", "sales.web_extract"
    run_id: str
    agent: str
    json_mode: bool = False
    max_tokens: int = 1200
    temperature: float = 0.2
    tools: list[dict] | None = None
    # Set only by runtime code that built every message from published content (web mock corpus,
    # public pages) while hwm is L1. The gateway then ignores commercial-money findings (decision D5);
    # names, secrets and canaries still raise the level. Ignored whenever hwm > L1.
    public_only: bool = False


@dataclass
class ChatResult:
    text: str
    tool_calls: list[dict]
    route: Route
    model: str
    level: Level
    usage: dict
    latency_ms: int
    audit_seq: int


@dataclass
class BackendReply:
    """What a backend (FakeLLM or OpenAICompatBackend) returns to the gateway."""

    text: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)  # prompt_tokens, completion_tokens, total_tokens
    model: str = ""


@dataclass(frozen=True)
class BackendCall:
    """Everything a backend may see. The level and purpose are metadata for the fake and for egress
    checks; the principal is deliberately absent (backends never need to know who asked)."""

    route: Route
    level: Level
    purpose: str
    messages: list[dict[str, Any]]
    json_mode: bool
    max_tokens: int
    temperature: float
    tools: list[dict] | None
