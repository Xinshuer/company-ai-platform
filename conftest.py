"""Root conftest: one set of shared fixtures for tests/ and evals/ (issues-log D-11).

tests/conftest.py (frozen core) defines pg, seeded, personas, fake_llm, app_client and anyio_backend
and pins the session to the fake LLM. This file registers that module once, as a global plugin named
"tests.conftest", so evals/ uses the very same fixtures and a run that collects both directories, in
either order (`pytest tests evals`, `pytest evals tests`), starts one database and seeds it once.

pytest would also find tests/conftest.py as the conftest of tests/ and register the same module a
second time, which is an error. A placeholder registered under its conftest name tells pytest that
this conftest is already loaded; the placeholder holds no fixtures or hooks.

Live evals (real DeepSeek, costs money): run only eval paths, for example

    CORPAI_LLM=live CORPAI_EVAL_RUNS=3 python -m pytest evals/legal -m eval

When CORPAI_LLM=live and every path on the command line lies under evals/, tests/conftest.py is
loaded without taking the API key out of the environment and the session stays in live mode. In
every other run (no paths, or any path outside evals/) tests/conftest.py applies its rules as
always: fake LLM, fake embedder, no API key in the process.
"""

from __future__ import annotations

import importlib
import os
import types
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent  # not resolved: keeps the drive-letter spelling pytest uses
_CORE_CONFTEST = _ROOT / "tests" / "conftest.py"
_CORE_MODULE = "tests.conftest"
_KEY = "DEEPSEEK_API_KEY"
# Stands in for tests/conftest.py under its conftest registration name; see the module docstring.
_PLACEHOLDER = types.ModuleType("tests_conftest_registered_globally")
_mode = {"live": False}


def live_evals_only(args: list[str], invocation_dir: Path, *, llm_mode: str | None) -> bool:
    """True when the run asks for the live model and names only paths inside evals/."""
    if llm_mode != "live" or not args:
        return False
    evals = (_ROOT / "evals").resolve()
    for arg in args:
        path = Path(arg.split("::", 1)[0])
        full = (invocation_dir / path).resolve()
        if full != evals and evals not in full.parents:
            return False
    return True


class _EnvKeepingApiKey:
    """os.environ while tests/conftest.py is imported for a live eval run: every read and write
    passes through, except that removing the API key does nothing. The key's value is never read."""

    def __init__(self, env: Any) -> None:
        self._env = env

    def __getattr__(self, name: str) -> Any:
        return getattr(self._env, name)

    def __getitem__(self, key: str) -> str:
        return self._env[key]

    def __setitem__(self, key: str, value: str) -> None:
        self._env[key] = value

    def __delitem__(self, key: str) -> None:
        if key != _KEY:
            del self._env[key]

    def __contains__(self, key: object) -> bool:
        return key in self._env

    def __iter__(self):
        return iter(self._env)

    def __len__(self) -> int:
        return len(self._env)

    def get(self, key: str, default: Any = None) -> Any:
        return self._env.get(key, default)

    def setdefault(self, key: str, default: str) -> str:
        return self._env.setdefault(key, default)

    def pop(self, key: str, *default: Any) -> Any:
        if key == _KEY:
            return default[0] if default else None
        return self._env.pop(key, *default)


def _import_core(live: bool) -> types.ModuleType:
    if not live:
        return importlib.import_module(_CORE_MODULE)
    real = os.environ
    os.environ = _EnvKeepingApiKey(real)  # type: ignore[assignment]
    try:
        mod = importlib.import_module(_CORE_MODULE)
    finally:
        os.environ = real
    os.environ["CORPAI_LLM"] = "live"  # tests/conftest.py pinned fake; this run asked for live
    from corpai.config import reload_settings

    reload_settings()
    return mod


def pytest_addhooks(pluginmanager) -> None:
    # Runs while this conftest is being registered: before pytest can reach tests/conftest.py.
    if pluginmanager.get_plugin(_CORE_MODULE) is not None:
        return
    config = pluginmanager.get_plugin("pytestconfig")
    args = list(getattr(getattr(config, "known_args_namespace", None), "file_or_dir", None) or [])
    invocation_dir = Path(getattr(getattr(config, "invocation_params", None), "dir", None) or os.getcwd())
    live = live_evals_only(args, invocation_dir, llm_mode=os.environ.get("CORPAI_LLM"))
    _mode["live"] = live
    if pluginmanager.get_plugin(str(_CORE_CONFTEST)) is None:
        pluginmanager.register(_PLACEHOLDER, str(_CORE_CONFTEST))
    pluginmanager.register(_import_core(live), _CORE_MODULE)


def pytest_report_header(config) -> str:
    from corpai.config import settings

    if _mode["live"]:
        return "corpai: CORPAI_LLM=live with eval paths only: evals call the real model (costs money)"
    return f"corpai: model {settings().llm_mode}, embedder {settings().embed_mode}"
