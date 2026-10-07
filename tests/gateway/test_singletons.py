"""get_gateway() / fake_llm() wiring and config parsing."""

from __future__ import annotations

import pytest

from corpai.config import reload_settings
from corpai.gateway import FakeLLM, OpenAICompatBackend, fake_llm, get_gateway, load_config
from corpai.gateway.conf import parse_config
from corpai.types import Route


def test_fake_mode_uses_the_fake_singleton():
    gw = get_gateway()
    assert gw is get_gateway()
    assert isinstance(gw.backend, FakeLLM) and gw.backend is fake_llm()


def test_conftest_fake_llm_fixture_is_the_singleton(fake_llm):
    assert fake_llm is get_gateway().backend


def test_live_mode_uses_openai_compat_backend(monkeypatch):
    monkeypatch.setenv("CORPAI_LLM", "live")
    reload_settings()
    try:
        assert isinstance(get_gateway().backend, OpenAICompatBackend)
    finally:
        monkeypatch.setenv("CORPAI_LLM", "fake")
        reload_settings()
    assert isinstance(get_gateway().backend, FakeLLM)


def test_models_yaml_matches_spec():
    cfg = load_config()
    local, ext = cfg.route(Route.LOCAL), cfg.route(Route.EXTERNAL)
    assert (local.model, ext.model) == ("deepseek-flash", "deepseek-flash")
    assert (local.max_concurrency, local.queue_timeout_s, local.simulated_onprem) == (2, 20, True)
    assert (ext.max_concurrency, ext.queue_timeout_s) == (8, 5)
    assert local.api_key_env == ext.api_key_env == "DEEPSEEK_API_KEY"
    assert cfg.extra_body == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize("mutate", [
    lambda raw: raw["routes"].pop("local"),
    lambda raw: raw["routes"]["external"].update(max_concurrency=0),
    lambda raw: raw["routes"]["external"].update(base_url=""),
    lambda raw: raw.update(retry={"max_attempts": -1}),
])
def test_bad_models_yaml_rejected(mutate):
    import copy

    from corpai.config import load_yaml
    raw = copy.deepcopy(load_yaml("models"))
    mutate(raw)
    with pytest.raises(ValueError):
        parse_config(raw)
