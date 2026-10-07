"""Quotes shown to the lawyer must exist in the contract, character for character.

A model may paraphrase, merge sentences or invent a quote. Every candidate is located in the
original text (exact, then whitespace-insensitive, which only tolerates re-wrapped lines); the
stored quote is the original substring, so `contract[start:end] == text` always holds. Anything
that cannot be located is dropped and counted, never shown as a quote.
"""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import BaseModel

MIN_QUOTE = 4
MAX_QUOTE = 600


class QuoteSpan(BaseModel):
    start: int
    end: int
    text: str


Window = tuple[int, int]


def _overlap(a: Window, b: Window) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def locate(text: str, quote: str, *, region: Window | None = None, regions: Iterable[Window] | None = None,
           forbidden: Iterable[Window] = ()) -> Window | None:
    """Span of `quote` in `text`, or None.

    `regions` (or the single `region`) limit where the quote may be found: a quote for a clause
    verdict must come from that clause (or a clause the review really consulted), never from
    elsewhere in the contract. With neither, the whole text is searched. A match that overlaps a
    `forbidden` span (an embedded instruction outside the clause under review) does not count, so
    an attacker's sentence cannot be shown as verified evidence for another clause."""
    if not isinstance(quote, str):
        return None
    q = quote.strip()
    if len(q) < MIN_QUOTE or len(q) > MAX_QUOTE:
        return None
    if regions is not None:
        windows = [w for w in regions if w]
    elif region is not None:
        windows = [region]
    else:
        windows = [(0, len(text))]
    bad = [f for f in forbidden if f]
    # Exact text first, then whitespace-insensitive (re-wrapped lines only); same characters either way.
    exact = re.compile(re.escape(q))
    loose = re.compile(r"\s+".join(re.escape(tok) for tok in q.split()))
    for pattern in (exact, loose):
        for lo, hi in windows:
            for m in pattern.finditer(text, lo, hi):
                if not any(_overlap(m.span(), f) for f in bad):
                    return m.start(), m.end()
    return None


def verified(text: str, candidates: Iterable[str | None], *, region: Window | None = None,
             regions: Iterable[Window] | None = None, forbidden: Iterable[Window] = (),
             limit: int = 3) -> tuple[list[QuoteSpan], int]:
    """(verified quote spans, number of candidates rejected). Duplicates count once."""
    out: list[QuoteSpan] = []
    rejected = 0
    seen: set[Window] = set()
    regions = list(regions) if regions is not None else None
    forbidden = list(forbidden)
    for cand in candidates:
        if cand is None or (isinstance(cand, str) and not cand.strip()):
            continue
        span = (locate(text, cand, region=region, regions=regions, forbidden=forbidden)
                if isinstance(cand, str) else None)
        if span is None:
            rejected += 1
            continue
        if span in seen or len(out) >= limit:
            continue
        seen.add(span)
        out.append(QuoteSpan(start=span[0], end=span[1], text=text[span[0]:span[1]]))
    return out, rejected


def region_of(text: str, body: str) -> tuple[int, int] | None:
    """Where a clause body (as split from the normalised text) sits in the original text."""
    body = body.strip()
    if not body:
        return None
    i = text.find(body)
    if i >= 0:
        return i, i + len(body)
    toks = body.split()
    if not toks:
        return None
    m = re.compile(r"\s+".join(re.escape(t) for t in toks)).search(text)
    return (m.start(), m.end()) if m else None
