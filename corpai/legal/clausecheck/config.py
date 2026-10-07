"""Runtime settings for the vendored Clause Check (see NOTICE.md for what changed).

Mode:
  gateway  - the corpai ModelGateway (corpai.legal.backend.GatewayBackend). The default.
  local    - any OpenAI-compatible endpoint, here the gateway's own /v1/chat/completions,
             reached by the unchanged OpenAICompatBackend with a gateway token as the API key.
  mock     - never call a model. Deterministic rule-based reviewer; for tests/UI work.

Unlike the original, nothing is read from the environment or a .env file: the platform
holds no provider keys in application code (the gateway reads them at call time), and the
caller passes every value explicitly. The Anthropic and DeepSeek modes and the auto-detection
probe are gone for the same reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "gateway"
MODES = ("gateway", "local", "mock")
COMPAT_MODES = ("local",)

FREE = {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    mode: str = "gateway"
    prompt_version: str = "v2"
    max_steps: int = 6
    max_tokens: int = 1200

    # --- OpenAI-compatible endpoint (mode "local") ----------------------------
    local_base_url: str = "http://127.0.0.1:8000/v1"
    local_model: str = "gateway"
    local_api_key: str = field(default="", repr=False)  # a gateway token; never printed

    # --- shared by the OpenAI-compatible path ----------------------------------
    compat_temperature: float = 0.2
    compat_max_tokens: int = 1200
    # json_schema = grammar-constrained; json_object = "valid JSON, schema described in the prompt".
    json_mode: str = ""
    # Put the whole contract in the system prompt instead of an index the model reads from via get_clause.
    full_contract: bool = False

    # No default playbook. The original pointed at its own customer-side playbook, which is not
    # vendored; defaulting to Kestrelbyte's provider-side rules (corpai/legal/data/playbook.json)
    # would silently change what the unchanged pipeline/mock reviewer decides. Callers pass a
    # Playbook (corpai does) or a path; pipeline.review_contract without either fails loudly.
    playbook_path: Path | None = None

    # resolved for the OpenAI-compatible backend (set in __post_init__)
    compat_base_url: str = ""
    compat_api_key: str = field(default="", repr=False)
    compat_json_mode: str = ""

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if self.prompt_version not in ("v1", "v2", "v3"):
            raise ValueError(f"prompt_version must be v1, v2 or v3, got {self.prompt_version!r}")
        if self.json_mode not in ("", "json_schema", "json_object"):
            raise ValueError(f"json_mode must be json_schema or json_object, got {self.json_mode!r}")
        if self.mode == "local":
            self.model = self.local_model
            self.compat_base_url = self.local_base_url
            self.compat_api_key = self.local_api_key
            self.compat_json_mode = self.json_mode or "json_schema"
        elif self.mode == "mock":
            self.model = "rule-based"

    @property
    def mock(self) -> bool:
        return self.mode == "mock"

    @property
    def compat(self) -> bool:
        return self.mode in COMPAT_MODES

    def price(self) -> dict:
        # Cost accounting lives in the gateway's per-department budget, not here.
        return FREE
