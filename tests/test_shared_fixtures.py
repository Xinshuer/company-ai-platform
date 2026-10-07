"""The root conftest.py shares tests/conftest.py with evals/ (issues-log D-11).

What this proves: the shared module is registered once, globally; pytest's own conftest discovery
for tests/ does not register it a second time; every shared session fixture has exactly one
definition (so `pytest tests evals` and `pytest evals tests` start one database); and the live-mode
switch only opens for eval paths with CORPAI_LLM=live.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ("anyio_backend", "pg", "seeded", "personas", "fake_llm", "app_client")


@pytest.fixture
def root_conftest(request):
    # The registered root conftest module (pytest may drop sys.modules["conftest"] while loading
    # other conftests, so `import conftest` could give a second copy).
    found = [p for p in request.config.pluginmanager.get_plugins()
             if getattr(p, "__file__", None) and Path(p.__file__).resolve() == ROOT / "conftest.py"]
    assert len(found) == 1
    return found[0]


def test_tests_conftest_is_one_global_plugin(request, root_conftest):
    pm = request.config.pluginmanager
    mod = pm.get_plugin("tests.conftest")
    assert mod is not None and Path(mod.__file__).resolve() == ROOT / "tests" / "conftest.py"
    placeholder = pm.get_plugin(str(root_conftest._CORE_CONFTEST))
    assert placeholder is root_conftest._PLACEHOLDER and placeholder is not mod
    assert not [n for n in vars(placeholder) if not n.startswith("__")], "the placeholder defines nothing"


@pytest.mark.parametrize("name", SHARED)
def test_each_shared_fixture_has_one_definition(request, name):
    defs = request._fixturemanager.getfixturedefs(name, request.node) or ()
    ours = [d for d in defs if d.func.__module__ == "tests.conftest"]
    assert len(ours) == 1, name  # anyio's own anyio_backend may come before it; ours is the one used
    assert defs[-1] is ours[0] and ours[0].baseid == "", f"{name} must be global so evals/ sees it"


def test_this_session_is_pinned_to_the_fake_model():
    # a run that collects tests/ never uses the paid model and holds no API key
    from corpai.config import settings

    assert settings().llm_mode == "fake"
    assert "DEEPSEEK_API_KEY" not in os.environ


@pytest.mark.parametrize("args,mode,expected", [
    (["evals/legal"], "live", True),
    (["evals"], "live", True),
    (["evals/legal/test_legal_eval.py::test_case"], "live", True),
    (["evals/legal", "evals/hr"], "live", True),
    ([], "live", False),                                  # testpaths include tests/
    (["evals/legal", "tests/audit"], "live", False),      # anything under tests/ keeps the fake model
    (["tests"], "live", False),
    (["."], "live", False),
    (["evals/../tests"], "live", False),
    (["evals/legal"], "fake", False),
    (["evals/legal"], None, False),
])
def test_live_mode_only_for_eval_paths(root_conftest, args, mode, expected):
    assert root_conftest.live_evals_only(args, ROOT, llm_mode=mode) is expected


def test_environment_guard_never_removes_the_key_but_passes_everything_else(root_conftest):
    env = {"DEEPSEEK_API_KEY": "placeholder-not-a-key", "CORPAI_LLM": "live", "X": "1"}
    guard = root_conftest._EnvKeepingApiKey(env)
    assert guard.pop("DEEPSEEK_API_KEY", None) is None  # returns the default, never the value
    del guard["DEEPSEEK_API_KEY"]
    assert "DEEPSEEK_API_KEY" in env
    guard["CORPAI_LLM"] = "fake"
    assert guard.pop("X") == "1" and "X" not in env
    assert guard.get("CORPAI_LLM") == "fake" and guard.setdefault("Y", "2") == "2" and env["Y"] == "2"
