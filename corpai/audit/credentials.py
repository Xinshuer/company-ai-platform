"""Keep credentials out of the audit log and traces (ENGINEERING.md security rule 4).

Tokens, session cookies and API keys must never be written to audit details or span attrs. The
check is structural (key names, anywhere in nested data) plus a few unmistakable value shapes
(JWTs, bearer headers, sk- API keys, PEM private keys). The same key rule runs again inside the
database (audit.has_credential_key), so a caller that bypasses this module is still refused.

Error messages name the offending *path*, never the value: the value is the secret.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

CREDENTIAL_WORDS: tuple[str, ...] = (
    "token", "authorization", "api_key", "apikey", "password", "passwd", "secret", "cookie",
    "session", "bearer", "jwt", "credential", "credentials", "private_key",
)

# Must stay identical to the regex in migrations/001_a1_audit_log.sql (audit.has_credential_key).
_KEY_RE = re.compile(r"(^|_)(" + "|".join(CREDENTIAL_WORDS) + r")(_|$)")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_VALUE_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\."      # JWT header.payload.
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"               # Authorization: Bearer ...
    r"|\bsk-[A-Za-z0-9_-]{16,}"                         # OpenAI-style / DeepSeek API keys
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----",             # PEM private keys
    re.IGNORECASE,
)


def is_credential_key(key: object) -> bool:
    """True for keys like token, access_token, apiKey, X-Api-Key, Set-Cookie, session_id.

    Whole words only, so usage counters such as ``tokens`` or ``prompt_tokens`` stay allowed.
    """
    s = _CAMEL_RE.sub(r"\1_\2", str(key)).lower()
    return bool(_KEY_RE.search(_NON_ALNUM_RE.sub("_", s)))


def looks_like_credential(value: str) -> bool:
    return bool(_VALUE_RE.search(value))


def find_credential(obj: Any, path: str = "$") -> str | None:
    """Path of the first credential-like key or value in nested data, or None."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if is_credential_key(k):
                return p
            hit = find_credential(v, p)
            if hit:
                return hit
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for i, v in enumerate(obj):
            hit = find_credential(v, f"{path}[{i}]")
            if hit:
                return hit
    elif isinstance(obj, str) and looks_like_credential(obj):
        return path
    return None


def check_no_credentials(obj: Any, what: str) -> None:
    """Raise ValueError if `obj` carries anything credential-like. `what` names the field."""
    hit = find_credential(obj, what)
    if hit:
        raise ValueError(f"{hit}: credentials must never be written to the audit log or traces "
                         "(store a token's jti instead)")
