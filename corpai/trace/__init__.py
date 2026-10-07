"""Trace spans, masked by level (docs/architecture.md §9). Public API re-exported here.

`tracer` is the process-wide default (default masker = corpai.classify); tests and special
callers build their own `Tracer(masker=...)`.
"""

from __future__ import annotations

from corpai.trace.read import dept_stats, read_meta, read_run
from corpai.trace.span import KINDS, Masker, Span, Tracer, default_masker

tracer = Tracer()

__all__ = ["KINDS", "Masker", "Span", "Tracer", "default_masker", "dept_stats", "read_meta", "read_run",
           "tracer"]
