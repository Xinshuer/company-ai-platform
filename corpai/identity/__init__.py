"""Identity: directory lookups, effective groups, session / tool / gateway tokens (architecture §5).

Public API: `directory.get_principal`, `directory.get_principal_sync`, `directory.list_personas`,
`groups.effective_groups`, `tokens.*`.
"""

from __future__ import annotations

from corpai.identity import directory, groups, tokens
from corpai.identity.directory import get_principal, get_principal_sync, list_personas, list_personas_sync
from corpai.identity.groups import effective_groups
from corpai.identity.tokens import (
    BadSignature,
    GatewayClaims,
    NotInScope,
    SessionClaims,
    SessionRevoked,
    TokenError,
    TokenExpired,
    ToolClaims,
    WrongAudience,
)

__all__ = [
    "directory", "groups", "tokens",
    "get_principal", "get_principal_sync", "list_personas", "list_personas_sync", "effective_groups",
    "TokenError", "TokenExpired", "WrongAudience", "BadSignature", "NotInScope", "SessionRevoked",
    "SessionClaims", "ToolClaims", "GatewayClaims",
]
