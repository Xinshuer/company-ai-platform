"""Person-name detection: a gazetteer of known people plus an honorific heuristic.

The gazetteer holds the full names of employees (core.users), CRM contacts and candidates. It is
matched case-sensitively and as whole words so that ordinary words never become "names". Latin
names need at least two tokens (a lone "Becker" is too ambiguous); CJK names need two characters.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Iterable

_lock = threading.Lock()
_WS = re.compile(r"\s+")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

# (latin regex | None, cjk regex | None, number of names). Swapped atomically.
_state: tuple[re.Pattern[str] | None, re.Pattern[str] | None, int] = (None, None, 0)

HONORIFIC = re.compile(
    r"(?<![A-Za-z])(?:Mr|Ms|Mrs|Dr|Prof|Herr|Frau)\.?[ \t]{1,3}"
    r"(?:(?:Dr|Prof)\.?[ \t]{1,3})?"
    r"[A-ZÄÖÜ][a-zäöüßéèáàíóúñçø'’-]{1,40}(?:[ -][A-ZÄÖÜ][a-zäöüßéèáàíóúñçø'’-]{1,40})?"
)


def _norm(name: str) -> str | None:
    name = _WS.sub(" ", str(name)).strip()
    if len(name) < 2:
        return None
    if _CJK.search(name):
        return name if len(name.replace(" ", "")) >= 2 else None
    tokens = name.split(" ")
    if len(tokens) < 2 or len(name) < 5:
        return None
    if not tokens[0][:1].isupper() or not tokens[-1][:1].isupper():
        return None
    return name


def _trie_regex(words: Iterable[str]) -> str:
    """A prefix-trie alternation: linear in the text, unlike a flat |-list of hundreds of names."""
    trie: dict[str, Any] = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = {}

    def build(node: dict[str, Any]) -> str:
        alts: list[str] = []
        end = False
        for ch in sorted(node):
            if ch == "":
                end = True
                continue
            piece = r"\s+" if ch == " " else re.escape(ch)  # names wrap across lines in documents
            alts.append(piece + build(node[ch]))
        if not alts:
            return ""
        if len(alts) == 1 and not end:
            return alts[0]
        body = "(?:" + "|".join(alts) + ")"
        return body + "?" if end else body

    return build(trie)


def set_gazetteer(names: Iterable[str]) -> int:
    """Replace the gazetteer. Returns how many names are active."""
    global _state
    latin: set[str] = set()
    cjk: set[str] = set()
    for raw in names:
        if raw is None:
            continue
        n = _norm(raw)
        if n is None:
            continue
        (cjk if _CJK.search(n) else latin).add(n)
    lat_re = re.compile(r"(?<![\w])" + _trie_regex(latin) + r"(?![\w])") if latin else None
    # Chinese has no spaces between words, so no word boundaries for CJK names.
    cjk_re = re.compile(_trie_regex(cjk)) if cjk else None
    with _lock:
        _state = (lat_re, cjk_re, len(latin) + len(cjk))
    return len(latin) + len(cjk)


def gazetteer_size() -> int:
    return _state[2]


def find_names(text: str) -> list[tuple[int, int]]:
    """Spans of person names: gazetteer hits and honorific + capitalised name."""
    lat_re, cjk_re, _ = _state
    spans: list[tuple[int, int]] = []
    if lat_re is not None:
        spans.extend(m.span() for m in lat_re.finditer(text))
    if cjk_re is not None:
        spans.extend(m.span() for m in cjk_re.finditer(text))
    spans.extend(m.span() for m in HONORIFIC.finditer(text))
    return spans


# ---- loading from the database ------------------------------------------------------------

# The tables the gazetteer is built from. All of them must be readable: a gazetteer silently
# built from fewer tables would let those people's names through as L1 (fail open).
_TABLES: tuple[tuple[str, str], ...] = (("core", "users"), ("crm", "contacts"), ("hr", "candidates"))


class GazetteerError(RuntimeError):
    """The gazetteer could not be loaded completely; the previous one stays in place."""


def _col(row: Any, key: str, idx: int = 0) -> Any:
    return row[key] if isinstance(row, dict) else row[idx]


def _names_from_table(conn: Any, schema: str, table: str) -> list[str] | None:
    """Names in one table, or None when the table does not exist or has no name column."""
    from psycopg import sql

    reg = conn.execute("SELECT to_regclass(%s) AS r", (f"{schema}.{table}",)).fetchone()
    if reg is None or _col(reg, "r") is None:
        return None
    cols = {_col(r, "column_name") for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (schema, table)).fetchall()}
    ident = sql.Identifier(schema, table)
    if "full_name" in cols:
        expr = sql.Identifier("full_name")
    elif "name" in cols:
        expr = sql.Identifier("name")
    elif "first_name" in cols and "last_name" in cols:
        expr = sql.SQL("concat_ws(' ', {}, {})").format(sql.Identifier("first_name"), sql.Identifier("last_name"))
    else:
        return None
    where = sql.SQL("")
    if "role" in cols and (schema, table) == ("core", "users"):
        where = sql.SQL(" WHERE role <> 'service'")  # service accounts are not people
    q = sql.SQL("SELECT {} AS n FROM {}").format(expr, ident) + where
    return [n for r in conn.execute(q).fetchall() if (n := _col(r, "n"))]


def load_gazetteer(conn: Any) -> int:
    """Load names from the directory, CRM contacts and candidates through `conn` (owner role).

    All-or-nothing: if any source table is missing, has no name column or cannot be read, raise
    GazetteerError naming the tables and keep the previous gazetteer (never a smaller one).
    """
    import psycopg

    names: list[str] = []
    failed: list[str] = []
    for schema, table in _TABLES:
        try:
            with conn.transaction():  # a savepoint, so one bad table does not abort the connection
                got = _names_from_table(conn, schema, table)
        except psycopg.Error as e:
            failed.append(f"{schema}.{table} ({type(e).__name__})")
            continue
        if got is None:
            failed.append(f"{schema}.{table} (missing)")
            continue
        names.extend(got)
    if failed:
        raise GazetteerError("person-name gazetteer not loaded; unreadable tables: " + ", ".join(failed))
    return set_gazetteer(names)


def load_gazetteer_from_db() -> int:
    """Owner-role job (synth, ingestion, API startup): refresh the gazetteer from the database."""
    from corpai.db import pg

    with pg.connect_owner() as conn:
        return load_gazetteer(conn)
