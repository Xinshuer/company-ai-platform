"""Tracer and Span (docs/architecture.md §9).

Text is masked once, when the span ends, with the span's *final* level: a span that starts at L2
and pulls in L4 data must not have stored its L2-masked input already. So the start write holds
metadata only and the end write carries the masked text. L4 text is never stored (the tracer
drops it and a CHECK constraint in trace.spans refuses it).

Spans are written through trace.write_span() on their own short-lived autocommit connection with
the principal's session variables, so a rolled-back business transaction keeps its trace and the
database decides whose span it is.

A span also records the GDPR data subjects of what it read (Span.add_subjects: the subject_ids of
tool rows and retrieved documents), so an erasure request reaches traces too (migration 103,
trace.erase_subject). Subjects are erasure metadata: no trace reader returns them.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Iterable

import psycopg

from corpai import clock
from corpai.audit.credentials import check_no_credentials, is_credential_key
from corpai.ids import new_id
from corpai.types import Level, Principal

log = logging.getLogger(__name__)

KINDS: frozenset[str] = frozenset({"node", "llm", "tool", "retrieval", "policy"})
Masker = Callable[[str, Level], "str | None"]

_MAX_ATTR_KEY = 64
_MAX_ATTR_BYTES = 4096  # attrs are metadata; a cap keeps content from being smuggled in bulk
MAX_SUBJECTS = 500  # trace.spans CHECK; more are dropped and the span says so (attrs.subjects_truncated)
_SUBJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,63}")  # row ids (fullmatch), as trace.write_span checks

_WRITE_SQL = (
    "SELECT trace.write_span(%s::text, %s::text, %s::text, %s::text, %s::text, %s::smallint, "
    "%s::timestamptz, %s::timestamptz, %s::text, %s::jsonb, %s::text, %s::text, %s::text[])"
)
_SET = "SELECT set_config(%s, %s, true)"


def default_masker(text: str, level: Level) -> str | None:
    """scrub_secrets + mask_for_level from corpai.classify, imported lazily.

    Fails closed: if classify is unavailable or raises, no text is stored at all.
    """
    try:
        from corpai.classify import mask_for_level, scrub_secrets
    except Exception:  # noqa: BLE001 - any import problem means "cannot mask", so store nothing
        return None
    try:
        scrubbed, _ = scrub_secrets(text)
        return mask_for_level(scrubbed, Level(level))
    except Exception:  # noqa: BLE001
        return None


def _check_attr(key: str, value: Any) -> Any:
    if not isinstance(key, str) or not key or len(key) > _MAX_ATTR_KEY:
        raise ValueError(f"span attr keys must be non-empty strings of at most {_MAX_ATTR_KEY} chars")
    if is_credential_key(key):
        raise ValueError(f"span attr {key!r}: credentials must never be written to traces")
    check_no_credentials(value, f"attrs.{key}")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"span attr {key!r} must be JSON-serialisable: {e}") from None
    if len(encoded.encode("utf-8")) > _MAX_ATTR_BYTES:
        raise ValueError(f"span attr {key!r} is larger than {_MAX_ATTR_BYTES} bytes; attrs are metadata, "
                         "put text in input/output so it gets masked")
    return json.loads(encoded)  # a detached copy: later mutation by the caller cannot bypass checks


class Span:
    """Handle yielded by Tracer.span(). Only metadata changes are allowed while it is open."""

    def __init__(self, *, span_id: str, run_id: str, parent_id: str | None, name: str, kind: str,
                 level: Level, input: str | None) -> None:
        self.span_id = span_id
        self.run_id = run_id
        self.parent_id = parent_id
        self.name = name
        self.kind = kind
        self._level = Level(level)
        self._input = input
        self._output: str | None = None
        self._attrs: dict[str, Any] = {}
        self._subjects: set[str] = set()

    @property
    def level(self) -> Level:
        return self._level

    @property
    def attrs(self) -> dict[str, Any]:
        return dict(self._attrs)

    def set_output(self, text: str | None) -> None:
        if text is not None and not isinstance(text, str):
            raise TypeError("span output must be a string")
        self._output = text

    def set_attr(self, key: str, value: Any) -> None:
        self._attrs[key] = _check_attr(key, value)

    def set_level(self, level: Level | int) -> None:
        """Raise the span's level. Lower values are ignored: levels only go up (rule 3)."""
        self._level = max(self._level, Level(level))

    def add_subjects(self, ids: Iterable[Any]) -> None:
        """Record data subjects (employee, contact, candidate ids) this span read. Like the level they
        only accumulate. Anything that is not a row id is ignored, so text never lands here."""
        for i in (ids,) if isinstance(ids, str) else ids or ():
            if not (isinstance(i, str) and _SUBJECT_RE.fullmatch(i)) or i in self._subjects:
                continue
            if len(self._subjects) >= MAX_SUBJECTS:
                self._attrs["subjects_truncated"] = True
                return
            self._subjects.add(i)


class Tracer:
    def __init__(self, masker: Masker | None = None) -> None:
        self._masker: Masker = masker or default_masker

    def _mask(self, text: str | None, level: Level) -> tuple[str | None, bool]:
        """(stored text, masking_failed). Fail closed on any masker problem."""
        if text is None or level >= Level.L4:
            return None, False
        try:
            out = self._masker(text, level)
        except Exception:  # noqa: BLE001 - a broken masker must never cause raw text to be stored
            return None, True
        if out is not None and not isinstance(out, str):
            return None, True
        return out, False

    async def _write(self, principal: Principal, span: Span, *, started_at: datetime,
                     ended_at: datetime | None, status: str, attrs: dict, input_text: str | None,
                     output_text: str | None) -> None:
        from corpai.db import pg

        params = (span.span_id, span.run_id, span.parent_id, span.name, span.kind, int(span.level),
                  started_at, ended_at, status, json.dumps(attrs, ensure_ascii=False, allow_nan=False),
                  input_text, output_text, sorted(span._subjects))
        conn = await psycopg.AsyncConnection.connect(pg.app_dsn(), autocommit=True)
        async with conn:
            async with conn.transaction():
                for name, value in (("app.user_id", principal.user_id), ("app.dept", str(principal.dept)),
                                    ("app.role", str(principal.role)), ("app.run_id", span.run_id)):
                    await conn.execute(_SET, (name, value))
                await conn.execute(_WRITE_SQL, params)

    async def _finish(self, principal: Principal, span: Span, started_at: datetime, status: str) -> None:
        level = span.level
        input_text, bad_in = self._mask(span._input, level)
        output_text, bad_out = self._mask(span._output, level)
        attrs = dict(span._attrs)
        if bad_in or bad_out:
            attrs["masking_failed"] = True
        await self._write(principal, span, started_at=started_at, ended_at=clock.now_utc(), status=status,
                          attrs=attrs, input_text=input_text, output_text=output_text)

    @asynccontextmanager
    async def span(self, name: str, *, kind: str, run_id: str, principal: Principal, level: Level,
                   parent_id: str | None = None, input: str | None = None) -> AsyncIterator[Span]:
        if kind not in KINDS:
            raise ValueError(f"span kind must be one of {sorted(KINDS)}")
        if not name or not run_id:
            raise ValueError("span name and run_id are required")
        if input is not None and not isinstance(input, str):
            raise TypeError("span input must be a string")
        sp = Span(span_id=new_id("span"), run_id=run_id, parent_id=parent_id, name=name, kind=kind,
                  level=Level(level), input=input)
        started_at = clock.now_utc()
        await self._write(principal, sp, started_at=started_at, ended_at=None, status="running",
                          attrs={}, input_text=None, output_text=None)
        try:
            yield sp
        except BaseException as exc:
            # Class name only: exception messages often quote the data being processed.
            sp._attrs["error_type"] = type(exc).__name__
            try:
                await self._finish(principal, sp, started_at, "error")
            except Exception as write_err:  # noqa: BLE001 - never mask the caller's exception
                log.warning("trace write failed for span %s: %s", sp.span_id, type(write_err).__name__)
            raise
        try:
            await self._finish(principal, sp, started_at, "ok")
        except Exception as write_err:  # noqa: BLE001
            # The body already succeeded (a model call was paid for, an egress row logged): turning a
            # trace write failure into an exception now would make callers retry and pay twice. The
            # span stays "running" in the table, which is itself visible to the platform admin.
            log.warning("trace write failed for span %s: %s", sp.span_id, type(write_err).__name__)
