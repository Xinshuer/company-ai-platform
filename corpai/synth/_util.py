"""Deterministic building blocks shared by the generators.

Everything here is a pure function of the seed: no OS clock, no OS entropy, no hash() of str
(PYTHONHASHSEED would change it). Each generator gets its own random.Random keyed by
"<seed>:<topic>", so adding rows in one module never shifts the numbers of another.
"""

from __future__ import annotations

import hashlib
import random
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Sequence, TypeVar

from faker import Faker

T = TypeVar("T")

# The simulation window (architecture §15). Fixed here instead of read from clock.today(), so a
# CORPAI_SIM_TODAY override can never change what the generator writes.
START = date(2025, 10, 1)
END = date(2026, 9, 30)
SIM_TODAY = date(2026, 10, 1)

STAFF_DOMAIN = "abctech.example"
# Salt of every random stream and canary. It is the company's former name (Kestrelbyte, renamed
# ABC Tech on 2026-10-07) and stays as it is: a new salt would change every generated amount,
# date and canary, and the same seed must keep giving the same company.
_SALT = "kestrelbyte"
CENT = Decimal("0.01")


def rng_for(seed: int, topic: str) -> random.Random:
    # str seeds go through sha512 in random.seed (version 2): stable across processes and platforms
    return random.Random(f"{_SALT}:{seed}:{topic}")


def faker_for(seed: int, locale: str, topic: str) -> Faker:
    fk = Faker(locale)
    digest = hashlib.sha256(f"faker:{seed}:{locale}:{topic}".encode()).digest()
    fk.seed_instance(int.from_bytes(digest[:8], "big"))
    return fk


def canary(seed: int, index: int) -> str:
    """KB-CANARY-XXXXXXXX: 8 uppercase hex chars derived from the seed (classify treats it as L4).
    KB- is from the former name Kestrelbyte, like _SALT, and stays for the same reason."""
    h = hashlib.sha256(f"{_SALT}-canary:{seed}:{index}".encode()).hexdigest()[:8].upper()
    return f"KB-CANARY-{h}"


_TRANSLIT = str.maketrans({"ł": "l", "Ł": "L", "ß": "ss", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE",
                           "đ": "d", "Đ": "D", "œ": "oe", "Œ": "OE"})


def ascii_fold(text: str) -> str:
    text = text.translate(_TRANSLIT)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def slug(text: str, sep: str = "-") -> str:
    out, prev_sep = [], False
    for ch in ascii_fold(text).lower():
        if ch.isalnum():
            out.append(ch)
            prev_sep = False
        elif not prev_sep and out:
            out.append(sep)
            prev_sep = True
    return "".join(out).strip(sep)


def money(x: float | int | Decimal) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def eur(x: Decimal | float | int) -> str:
    """Human formatting for documents: EUR 1,234,567.00."""
    return f"EUR {money(x):,.2f}"


def days(n: int) -> timedelta:
    return timedelta(days=n)


def rand_date(rng: random.Random, lo: date = START, hi: date = END) -> date:
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def at_utc(d: date, hour: int = 9, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), tzinfo=timezone.utc)


def months(start: date = START, end: date = END) -> list[date]:
    """First day of every month in [start, end]."""
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        out.append(d)
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return out


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def period(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def pick(rng: random.Random, seq: Sequence[T]) -> T:
    return seq[rng.randrange(len(seq))]


def weighted(rng: random.Random, pairs: Sequence[tuple[T, float]]) -> T:
    total = sum(w for _, w in pairs)
    x = rng.random() * total
    for item, w in pairs:
        x -= w
        if x < 0:
            return item
    return pairs[-1][0]


def split_amount(rng: random.Random, total: Decimal, parts: int) -> list[Decimal]:
    """Split a money amount into `parts` positive pieces that sum exactly to `total`."""
    if parts <= 1:
        return [money(total)]
    weights = [rng.uniform(0.5, 1.5) for _ in range(parts)]
    s = sum(weights)
    pieces = [money(total * Decimal(str(w / s))) for w in weights[:-1]]
    pieces.append(money(total) - sum(pieces, Decimal("0")))
    return pieces


@dataclass
class Doc:
    """One markdown file of the corpus. `meta` becomes the YAML front matter."""

    doc_id: str
    title: str
    collection: str
    source: str
    level: int
    dept: str | None
    owner_id: str | None
    acl_groups: list[str]
    trust: str
    body: str
    subject_ids: list[str] = field(default_factory=list)
    project_id: str | None = None
    matter_id: str | None = None
    version: int = 1
    status: str = "current"
    source_uri: str = ""

    @property
    def relpath(self) -> str:
        # Older versions share the doc_id, so they get a ".v<N>" file name next to the current one.
        name = self.doc_id if self.status != "superseded" else f"{self.doc_id}.v{self.version}"
        return f"docs/{self.collection}/{name}.md"

    def meta(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id, "title": self.title, "collection": self.collection, "source": self.source,
            "level": self.level, "dept": self.dept, "owner_id": self.owner_id, "acl_groups": list(self.acl_groups),
            "trust": self.trust, "subject_ids": list(self.subject_ids), "project_id": self.project_id,
            "matter_id": self.matter_id, "version": self.version, "status": self.status,
            "source_uri": self.source_uri,
        }


@dataclass
class World:
    """Everything one run generates, before it is written. Tables keep insertion order."""

    seed: int
    small: bool
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    docs: list[Doc] = field(default_factory=list)
    web_pages: list[dict[str, Any]] = field(default_factory=list)
    planted: list[dict[str, Any]] = field(default_factory=list)
    canaries: list[dict[str, Any]] = field(default_factory=list)
    memberships: set[tuple[str, str]] = field(default_factory=set)
    meta: dict[str, Any] = field(default_factory=dict)  # cross-module facts (e.g. résumé pairs) for planted.py

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.setdefault(table, [])

    def add(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        self.rows(table).append(row)
        return row

    def member(self, user_id: str, grp: str) -> None:
        self.memberships.add((user_id, grp))

    def by_id(self, table: str, key: str) -> dict[str, dict[str, Any]]:
        return {r[key]: r for r in self.rows(table)}


def label(level: int, dept: str | None, owner_id: str | None, acl: Iterable[str], *,
          trust: str = "internal", subjects: Iterable[str] = ()) -> dict[str, Any]:
    """The §4 label columns, ready to merge into a row dict. acl/subjects keep their given order."""
    acl_list = list(dict.fromkeys(acl))
    if not acl_list:
        raise ValueError("a labelled row needs at least one acl group (empty acl = nobody, use head groups)")
    return {"level": level, "dept": dept, "owner_id": owner_id, "acl_groups": acl_list,
            "trust": trust, "subject_ids": list(dict.fromkeys(subjects))}
