"""Secret detection and scrubbing (design-review §4 rule 5: secrets never reach a model).

Each pattern is a literal prefix followed by single character-class runs, so matching stays linear
even on a 1 MB adversarial input, and a long secret is removed whole (never just its first part).
"""

from __future__ import annotations

import re

from .normalise import spans_with_hidden

# (kind, pattern). Order matters only for readability: overlapping spans are merged before replacing.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(
        r"-----BEGIN (?:[A-Z0-9]+ ){0,3}PRIVATE KEY-----"
        # an unterminated block (truncated paste) is scrubbed to the end: fail closed
        r"(?:[\s\S]*?-----END (?:[A-Z0-9]+ ){0,3}PRIVATE KEY-----|[\s\S]*\Z)")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")),
    ("openai_key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")),
    ("aws_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])")),
    ("github_token", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}")),
    ("stripe_key", re.compile(r"(?<![A-Za-z0-9])(?:sk|rk)_live_[A-Za-z0-9]{16,}")),
    ("bearer", re.compile(r"(?i)(?<![A-Za-z0-9])bearer\s+(?!\[REDACTED:)[A-Za-z0-9._~+/-]{20,}=*")),
)

# key = value credentials. Only the value is replaced, so traces still show which field was there.
_ASSIGN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[a-z0-9]{1,40}[_-]){0,4}"
    r"(?:password|passwd|pwd|passwort|kennwort|secret|api[_-]?key|token)(?![A-Za-z0-9])"
    r"[\"']?\s{0,4}[:=]\s{0,4}(?!\[REDACTED:)(?P<value>[^\s\"',;]+|\"[^\"\n]+\"|'[^'\n]+')"
    r"|(?:密码|口令)\s{0,4}[:：=]\s{0,4}(?!\[REDACTED:)(?P<cnvalue>\S+)"
)


def find_secrets(text: str) -> list[tuple[int, int, str]]:
    """Spans (start, end, kind) of secrets, non-overlapping, sorted.

    Also finds secrets hidden by zero-width or fullwidth characters ("sk-abc<ZWSP>def…"): a model
    reads straight through those, so the span (hidden characters included) is reported in the raw
    text. classify_text uses the same normalisation, so the two never disagree.
    """
    return _merge(spans_with_hidden(text, _find_raw))


def _find_raw(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            spans.append((m.start(), m.end(), kind))
    for m in _ASSIGN.finditer(text):
        grp = "value" if m.group("value") is not None else "cnvalue"
        spans.append((m.start(grp), m.end(grp), "password"))
    return spans


def _merge(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    spans.sort(key=lambda s: (s[0], -s[1]))
    out: list[tuple[int, int, str]] = []
    for s, e, k in spans:
        if out and s < out[-1][1]:
            ps, pe, pk = out[-1]
            out[-1] = (ps, max(pe, e), pk)
        else:
            out.append((s, e, k))
    return out


def scrub_secrets(text: str) -> tuple[str, int]:
    """Replace every secret with [REDACTED:<kind>]. Returns (scrubbed text, number replaced).

    Idempotent: already redacted markers are not matched again.
    """
    spans = find_secrets(text)
    if not spans:
        return text, 0
    parts: list[str] = []
    pos = 0
    for s, e, kind in spans:
        parts.append(text[pos:s])
        parts.append(f"[REDACTED:{kind}]")
        pos = e
    parts.append(text[pos:])
    return "".join(parts), len(spans)
