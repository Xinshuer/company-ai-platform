"""Deterministic feature-hashing embedder for tests and the fake mode.

Lower-cased word unigrams and bigrams (and every CJK character as its own token) are hashed into
384 signed buckets and L2-normalised. Texts that share words are close, texts that share none are
near-orthogonal. It has no notion of meaning, so a Chinese question only finds an English document
through shared tokens (names, numbers, acronyms like "GDPR"); real cross-lingual retrieval needs the
multilingual model (CORPAI_EMBED=fastembed).
"""

from __future__ import annotations

import hashlib
import re

from corpai.embed.base import DIM, l2_normalise

_CJK = "㐀-䶿一-鿿豈-﫿"
# A CJK character is one token; any other run of letters/digits is one token. CJK must be excluded
# from the second class, otherwise "GDPR第17条" would become a single token.
_TOKEN = re.compile(rf"[{_CJK}]|[^\W_{_CJK}]+", re.UNICODE)

_STOP = frozenset(
    "a an and are as at be by can do does for from has have how i in is it its may me my of on or our "
    "should that the their there this to was we what when where which who why will with you your".split()
)

_EMPTY = "\x00empty"  # a text with no tokens still gets a unit vector (cosine with a zero vector is NaN)


def _norm_token(tok: str) -> str:
    tok = tok.lower()
    # A crude plural strip, applied the same way to queries and documents ("policies" ~ "policy" does
    # not match, but "contracts" ~ "contract" does). Good enough for a fake.
    if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss") and tok.isascii():
        tok = tok[:-1]
    return tok


def tokens(text: str) -> list[str]:
    out = []
    for m in _TOKEN.finditer(text):
        tok = _norm_token(m.group(0))
        if tok not in _STOP:
            out.append(tok)
    return out


def _bucket(feature: str) -> tuple[int, float]:
    # hashlib, not hash(): Python's str hash is salted per process and would break determinism
    h = int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")
    return h % DIM, (1.0 if (h >> 32) & 1 else -1.0)


class FakeEmbedder:
    name = "fake-hash-384-v1"
    dim = DIM

    def embed_one(self, text: str) -> list[float]:
        toks = tokens(text if isinstance(text, str) else str(text))
        feats: list[tuple[str, float]] = [("u:" + t, 1.0) for t in toks]
        feats += [("b:" + a + " " + b, 0.5) for a, b in zip(toks, toks[1:])]
        if not feats:
            feats = [(_EMPTY, 1.0)]
        vec = [0.0] * DIM
        for feat, weight in feats:
            idx, sign = _bucket(feat)
            vec[idx] += sign * weight
        if not any(vec):  # every feature cancelled out in signed buckets: fall back to a fixed direction
            idx, sign = _bucket(_EMPTY)
            vec[idx] = sign
        return l2_normalise(vec)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]
