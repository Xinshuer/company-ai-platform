"""Classification, DLP and masking (architecture §7, design-review §4).

Pure Python, no network. Public API:
    classify_text(text, *, floor=Level.L1) -> Classification     never raises; error → L4
    source_default(source) -> Level                               config/sources.yaml; unknown → L4
    scrub_secrets(text) -> (text, n)                              secrets → [REDACTED:<kind>]
    dlp_scan(text, *, route) -> DlpResult                         external blocked at L3+
    mask_for_level(text, level) -> str | None                     trace masking
    redact_protected(text) -> (text, findings)                    résumés before screening (D6)
    set_gazetteer(names) / load_gazetteer(conn) / load_gazetteer_from_db()   all-or-nothing (GazetteerError)
    gazetteer_size()                                              0 = not loaded: the gateway fails closed
"""

from __future__ import annotations

from .dlp import MAX_CHARS, classify_text, dlp_scan, mask_for_level
from .model import KINDS, Classification, DlpResult, Finding
from .names import GazetteerError, gazetteer_size, load_gazetteer, load_gazetteer_from_db, set_gazetteer
from .protected import redact_protected
from .secrets import find_secrets, scrub_secrets
from .sources import parse_level, source_default

__all__ = [
    "KINDS", "MAX_CHARS", "Classification", "DlpResult", "Finding", "GazetteerError",
    "classify_text", "dlp_scan", "find_secrets", "gazetteer_size", "load_gazetteer", "load_gazetteer_from_db",
    "mask_for_level", "parse_level", "redact_protected", "scrub_secrets", "set_gazetteer", "source_default",
]
