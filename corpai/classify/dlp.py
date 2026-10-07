"""classify_text, the egress DLP check and trace masking (architecture §7)."""

from __future__ import annotations

from typing import Any

from corpai.types import Level, Route

from . import detectors
from .model import Classification, DlpResult, Finding
from .secrets import scrub_secrets
from .sources import parse_level

# Above this the classifier does not try: like a timeout, too large means "treat as restricted".
MAX_CHARS = 4_000_000

_ERROR = Classification(Level.L4, (Finding("classifier_error", 0, 0, Level.L4),))


# Finding kinds that describe commercial facts. On content that is already published (company
# websites, press, funding news) they are public by definition, so `public=True` caps them at L1.
# Personal data, secrets, canaries and everything else still raise the level (decision D5).
PUBLIC_CAPPED_KINDS = frozenset({"money"})


def classify_text(text: str, *, floor: Level = Level.L1, public: bool = False) -> Classification:
    """Level = max(floor, every finding's level). Never raises: any internal error → L4.

    `public=True` only for text whose every part is published content (the web mock corpus,
    public site pages); it is set by code that knows the source, never from model output.
    """
    try:
        floor = parse_level(floor)
        if not isinstance(text, str) or len(text) > MAX_CHARS:
            return _ERROR
        findings = detectors.detect(text)
        if public is True:
            findings = [Finding(f.kind, f.start, f.end, Level.L1) if f.kind in PUBLIC_CAPPED_KINDS else f
                        for f in findings]
        level = max([floor, *(f.level for f in findings)])
        findings.sort(key=lambda f: (f.start, f.end, f.kind))
        return Classification(Level(level), tuple(findings))
    except Exception:  # fail closed (ENGINEERING.md security rule 3)
        return _ERROR


def dlp_scan(text: str, *, route: Route | str, public: bool = False) -> DlpResult:
    """The last check before egress. `external` is blocked at L3+; `local` is never blocked.

    An unknown route counts as external: a typo must not open the door. `public` as in classify_text.
    """
    c = classify_text(text, public=public)
    try:
        r: Route | None = Route(route)
    except ValueError:
        r = None
    blocked = r is not Route.LOCAL and c.level >= Level.L3
    return DlpResult(blocked=blocked, level=c.level, findings=c.findings)


def mask_for_level(text: str, level: Level | int | Any) -> str | None:
    """What a trace may store (design-review §2.11): L1/L2 as is, L3 masked, L4 nothing.

    The effective level is max(level, classify_text(text).level): a span labelled L2 whose text
    turns out to hold a salary is stored as L4 (rule 2, the higher level wins).
    """
    if text is None:
        return None
    try:
        given = parse_level(level)
    except ValueError:
        return None
    c = classify_text(text, floor=given)
    if c.level >= Level.L4:
        return None
    if c.level <= Level.L2:
        return text
    spans = sorted(((f.start, f.end, f.kind) for f in c.findings if f.level >= Level.L3),
                   key=lambda s: (s[0], -s[1]))
    parts: list[str] = []
    pos = 0
    for s, e, kind in spans:
        if e <= pos:
            continue  # inside a span already masked
        parts.append(text[pos:max(pos, s)])
        parts.append(f"⟨{kind}⟩")
        pos = e
    parts.append(text[pos:])
    masked, _ = scrub_secrets("".join(parts))
    return masked
