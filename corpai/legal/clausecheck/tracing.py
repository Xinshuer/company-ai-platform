"""No-op tracing hooks (the original wraps Langfuse; see NOTICE.md).

In corpai every model call is traced and audited by the gateway (masked by level), so the
review loop keeps its @observe / annotate call sites but they do nothing here. Sending
contract text to a hosted tracer would also be an unlisted egress.
"""
from __future__ import annotations

import functools
from typing import Any, Callable

ENABLED = False


def observe(func: Callable | None = None, **_kwargs: Any):  # type: ignore[misc]
    """Identity decorator; supports both @observe and @observe(name=...)."""

    def wrap(f: Callable) -> Callable:
        @functools.wraps(f)
        def inner(*a: Any, **k: Any) -> Any:
            return f(*a, **k)

        return inner

    return wrap(func) if callable(func) else wrap


def annotate(**_metadata: Any) -> None:
    return None


def flush() -> None:
    return None
