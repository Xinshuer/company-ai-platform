"""Hidden-character normalisation shared by every detector that must not be fooled by it.

Zero-width and bidi controls (category Cf), the soft hyphen and fullwidth ASCII hide data from
the patterns ("sk-abc<ZWSP>def…", "Ａｇｅ: 34", "KB-CANARY-0A1B<ZWSP>2C3D") while a model reads
straight through them. Text containing any of them is scanned a second time in normalised form,
with offsets mapped back to the original, by classify_text, scrub_secrets and redact_protected
alike, so the three never disagree about what is in a message.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, TypeVar

_HIDING_RANGES = ((0x00AD, 0x00AD), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0xFEFF, 0xFEFF),
                  (0xFF0B, 0xFF0B), (0xFF0D, 0xFF0E), (0xFF10, 0xFF19), (0xFF20, 0xFF3A), (0xFF41, 0xFF5A))
HIDING = re.compile("[" + "".join(f"\\u{a:04x}-\\u{b:04x}" for a, b in _HIDING_RANGES) + "]")

T = TypeVar("T")


def normalised(text: str) -> tuple[str, list[int]] | None:
    """NFKC per character without format characters, plus a map normalised index → original index.

    None when the text has no hiding character (the common case: nothing to do). The map has one
    extra entry, len(text), so an end offset equal to len(normalised) maps too.
    """
    if not HIDING.search(text):
        return None
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch < "\x80":
            chars.append(ch)
            index.append(i)
            continue
        if unicodedata.category(ch) == "Cf":
            continue
        for c in unicodedata.normalize("NFKC", ch):
            chars.append(c)
            index.append(i)
    index.append(len(text))
    return "".join(chars), index


def map_span(index: list[int], start: int, end: int) -> tuple[int, int]:
    """A [start, end) span of the normalised text as a span of the original text.

    The end maps to just after the original character of the last normalised one, so hidden
    characters *inside* the span are covered and the original span is never shorter.
    """
    s = index[start]
    e = index[end - 1] + 1 if end > start else s
    return s, e


def spans_with_hidden(text: str, find: Callable[[str], Iterable[tuple[int, int, T]]]) -> list[tuple[int, int, T]]:
    """`find` over the raw text plus over its normalised form, offsets in the raw text."""
    out = list(find(text))
    norm = normalised(text)
    if norm is not None:
        ntext, index = norm
        for s, e, tag in find(ntext):
            out.append((*map_span(index, s, e), tag))
    return out
