"""Playbooks: department rules as versioned data (phase-B spec; design-review 三 Playbook).

playbooks/<id>.yaml holds one or more YAML documents, one per version, each with
    id, title, dept, owner_role, version, status (draft|active|retired), level, eval_set, body
load_playbook(id) returns the single ACTIVE version; drafts and retired versions are never used by a
graph, but load_playbook(id, version=N) reads a specific one (for review and evals). The level is
max(declared, classify(body)) — a playbook is never labelled below what it contains — and every
graph output that used a playbook records `ref` ("<id>@<version>"). Passing `principal` applies
decide(READ) on the playbook's label (ACL dept:<d>, head:<d>, role:ceo; architecture §5.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from corpai.config import ROOT
from corpai.types import Action, Dept, Label, Level, Principal, Resource, Role, Trust

PLAYBOOK_DIR = ROOT / "playbooks"
STATUSES = ("draft", "active", "retired")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_KEYS = {"id", "title", "dept", "owner_role", "version", "status", "level", "eval_set", "body"}


class PlaybookError(ValueError):
    """A malformed playbook file (fails loudly: a broken rule set must not be half-used)."""


class PlaybookNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Playbook:
    id: str
    title: str
    dept: Dept
    owner_role: Role
    version: int
    status: str
    level: Level
    eval_set: str | None
    body: Any
    declared_level: Level = Level.L2
    acl_groups: frozenset[str] = field(default_factory=frozenset)

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def label(self) -> Label:
        return Label(level=self.level, dept=self.dept, acl_groups=self.acl_groups, trust=Trust.REVIEWED,
                     sources=(f"playbook:{self.ref}",))


def _level(v: Any) -> Level:
    from corpai.classify import parse_level

    try:
        return Level(parse_level(v))
    except Exception:  # noqa: BLE001 - re-raised with a fixed message
        raise PlaybookError(f"bad level {v!r}; use L1..L4") from None


def _parse(raw: Any, path: Path) -> Playbook:
    from corpai.classify import classify_text

    if not isinstance(raw, dict):
        raise PlaybookError(f"{path.name}: each document must be a mapping")
    missing = _KEYS - set(raw) - {"eval_set"}
    if missing:
        raise PlaybookError(f"{path.name}: missing {sorted(missing)}")
    extra = set(raw) - _KEYS
    if extra:
        raise PlaybookError(f"{path.name}: unknown keys {sorted(extra)}")
    pid = raw["id"]
    if not isinstance(pid, str) or not _ID_RE.match(pid) or path.stem != pid:
        raise PlaybookError(f"{path.name}: id must be a dotted lower-case name equal to the file name")
    try:
        dept, owner = Dept(raw["dept"]), Role(raw["owner_role"])
    except ValueError:
        raise PlaybookError(f"{path.name}: unknown dept or owner_role") from None
    if owner not in (Role.MANAGER, Role.CEO):
        raise PlaybookError(f"{path.name}: a playbook is owned by a head (manager) or the CEO")
    version = raw["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PlaybookError(f"{path.name}: version must be a positive integer")
    if raw["status"] not in STATUSES:
        raise PlaybookError(f"{path.name}: status must be one of {STATUSES}")
    if not isinstance(raw["title"], str) or not raw["title"].strip():
        raise PlaybookError(f"{path.name}: title is required")
    if raw["body"] in (None, "", {}, []):
        raise PlaybookError(f"{path.name}: body is empty")
    declared = _level(raw["level"])
    body_text = raw["body"] if isinstance(raw["body"], str) else yaml.safe_dump(raw["body"], allow_unicode=True)
    level = max(declared, Level(classify_text(f"{raw['title']}\n{body_text}").level))  # never below its content
    eval_set = raw.get("eval_set")
    if eval_set is not None and not isinstance(eval_set, str):
        raise PlaybookError(f"{path.name}: eval_set must be a path")
    acl = frozenset({f"dept:{dept}", f"head:{dept}", "role:ceo"})
    return Playbook(id=pid, title=raw["title"].strip(), dept=dept, owner_role=owner, version=version,
                    status=raw["status"], level=level, eval_set=eval_set, body=raw["body"], declared_level=declared,
                    acl_groups=acl)


def _file(playbook_id: str, directory: Path | None) -> Path:
    if not isinstance(playbook_id, str) or not _ID_RE.match(playbook_id):
        raise PlaybookNotFound("no such playbook")
    return (directory or PLAYBOOK_DIR) / f"{playbook_id}.yaml"


def load_versions(playbook_id: str, *, directory: Path | None = None) -> list[Playbook]:
    """Every version in the file, oldest first. At most one may be active; versions are unique."""
    path = _file(playbook_id, directory)
    if not path.is_file():
        raise PlaybookNotFound(f"no playbook {playbook_id!r}")
    try:
        docs = [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d is not None]
    except yaml.YAMLError:
        raise PlaybookError(f"{path.name}: not valid YAML") from None
    versions = sorted((_parse(d, path) for d in docs), key=lambda p: p.version)
    if not versions:
        raise PlaybookError(f"{path.name}: no versions")
    if len({v.version for v in versions}) != len(versions):
        raise PlaybookError(f"{path.name}: duplicate version numbers")
    if sum(v.status == "active" for v in versions) > 1:
        raise PlaybookError(f"{path.name}: more than one active version")
    if len({(v.dept, v.owner_role) for v in versions}) != 1:
        raise PlaybookError(f"{path.name}: every version must keep its dept and owner")
    return versions


def _check_read(principal: Principal | None, pb: Playbook) -> None:
    if principal is None:
        return
    from corpai.policy import decide

    dec = decide(principal, Action.READ, Resource(kind="playbook", id=pb.ref, label=pb.label))
    if not dec.allow:
        raise PermissionError(f"no permission to read playbook {pb.id}: {dec.reason}")


def load_playbook(playbook_id: str, *, version: int | None = None, principal: Principal | None = None,
                  directory: Path | None = None) -> Playbook:
    """The active version (or exactly `version`). PlaybookNotFound when there is none."""
    versions = load_versions(playbook_id, directory=directory)
    if version is None:
        found = next((v for v in versions if v.status == "active"), None)
        if found is None:
            raise PlaybookNotFound(f"playbook {playbook_id!r} has no active version")
    else:
        found = next((v for v in versions if v.version == version), None)
        if found is None:
            raise PlaybookNotFound(f"playbook {playbook_id!r} has no version {version}")
    _check_read(principal, found)
    return found


def list_playbooks(*, dept: Dept | str | None = None, statuses: Iterable[str] = ("active",),
                   directory: Path | None = None) -> list[Playbook]:
    wanted = set(statuses)
    out = []
    for path in sorted((directory or PLAYBOOK_DIR).glob("*.yaml")):
        for v in load_versions(path.stem, directory=directory):
            if v.status in wanted and (dept is None or str(v.dept) == str(dept)):
                out.append(v)
    return out
