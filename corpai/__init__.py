"""corpai: the simulated internal AI platform of ABC Tech AB (a fictional company)."""

import os as _os
import sys as _sys

# LangChain's own tracing (LangSmith, a US SaaS) would send the state of every graph run, up to L4,
# past config/egress.yaml, the egress log and trace masking. It is switched on by environment
# variables alone, so whatever imports corpai (serve, demo-record, evals, tests) forces them off
# before LangChain reads them (issues-log FN-25). LangSmith reads <LANGSMITH|LANGCHAIN>_TRACING_V2
# first, then _TRACING; langchain-core treats "false" as unset.
LANGSMITH_TRACING_VARS = ("LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING")


def _langsmith_off() -> None:
    on = [n for n in LANGSMITH_TRACING_VARS
          if _os.environ.get(n, "").strip().lower() not in ("", "0", "false")]
    if on:
        print(f"corpai: ignoring {', '.join(on)}: LangSmith tracing would send run data outside the egress "
              "allowlist (config/egress.yaml); it stays off", file=_sys.stderr)
    for n in LANGSMITH_TRACING_VARS:
        _os.environ[n] = "false"
    utils = _sys.modules.get("langsmith.utils")
    if utils is not None:  # imported before corpai: drop its cached reading of the variables
        utils.get_env_var.cache_clear()


_langsmith_off()
