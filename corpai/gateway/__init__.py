"""Model gateway (architecture §8): the only way model traffic leaves the process.

Public API:
    ChatRequest, ChatResult, ModelGateway.chat(req)      routing, DLP, budget, audit, trace, egress log
    get_gateway()                                        process singleton (FakeLLM or OpenAICompatBackend)
    FakeLLM, fake_llm()                                  deterministic backend for CORPAI_LLM=fake
    OpenAICompatBackend                                  live backend (openai SDK, key from env at call time)
    errors: GatewayError, LocalUnavailable, ExternalUnavailable, GatewayBusy, BudgetExceeded, BackendError,
            MissingApiKey, EgressDenied, RetryableBackendError, SecretDetected
    ops: get_switches(), read_switches(), set_switch(principal, name, on, reason)
    egress: check_egress(destination, url, level), inventory()
    openai_compat.router                                 POST /v1/chat/completions (import the module)
"""

from __future__ import annotations

import threading

from corpai.config import settings
from corpai.gateway import egress, ops
from corpai.gateway.backend import Backend, OpenAICompatBackend
from corpai.gateway.conf import GatewayConfig, RetryConfig, RouteConfig, load_config
from corpai.gateway.errors import (
    BackendError,
    BudgetExceeded,
    EgressDenied,
    ExternalUnavailable,
    GatewayBusy,
    GatewayError,
    LocalUnavailable,
    MissingApiKey,
    RetryableBackendError,
    SecretDetected,
)
from corpai.gateway.fake import FakeLLM, default_responder, fake_llm
from corpai.gateway.gateway import ModelGateway
from corpai.gateway.models import BackendCall, BackendReply, ChatRequest, ChatResult

_lock = threading.Lock()
_gateways: dict[str, ModelGateway] = {}


def get_gateway() -> ModelGateway:
    """The process gateway for the current CORPAI_LLM mode: FakeLLM for 'fake', the DeepSeek-backed
    OpenAICompatBackend for 'live'. Keyed by mode so reload_settings() in tests switches cleanly."""
    mode = settings().llm_mode
    with _lock:
        gw = _gateways.get(mode)
        if gw is None:
            backend: Backend = fake_llm() if mode == "fake" else OpenAICompatBackend()
            gw = _gateways[mode] = ModelGateway(backend)
        return gw


__all__ = [
    "Backend", "BackendCall", "BackendError", "BackendReply", "BudgetExceeded", "ChatRequest", "ChatResult",
    "EgressDenied", "ExternalUnavailable", "FakeLLM", "GatewayBusy", "GatewayConfig", "GatewayError",
    "LocalUnavailable",
    "MissingApiKey", "ModelGateway", "OpenAICompatBackend", "RetryConfig", "RetryableBackendError",
    "RouteConfig", "SecretDetected", "default_responder", "egress", "fake_llm", "get_gateway", "load_config", "ops",
]
