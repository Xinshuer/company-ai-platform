"""Read the synth corpus: <corpus_dir>/docs/<collection>/<doc_id>.md (superseded: <doc_id>.v<N>.md),
each with YAML front matter (architecture §15, corpai/synth/writer.py render_doc)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from corpai.rag.model import COLLECTIONS, STATUSES, DocIn, IngestError
from corpai.types import Dept, Label, Level, Trust


def split_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    raw = raw.replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        raise ValueError("no YAML front matter")
    end = raw.find("\n---\n", 4)
    if end == -1:
        raise ValueError("unterminated YAML front matter")
    meta = yaml.safe_load(raw[4:end + 1])
    if not isinstance(meta, dict):
        raise ValueError("front matter is not a mapping")
    return meta, raw[end + 5:].lstrip("\n")


def _level(value: Any) -> Level:
    # A missing or malformed level fails closed to L4 instead of guessing low.
    try:
        return Level(int(value))
    except (TypeError, ValueError):
        return Level.L4


def _str_list(meta: dict, key: str) -> frozenset[str]:
    value = meta.get(key) or []
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return frozenset(value)


def doc_from_file(path: Path) -> DocIn:
    try:
        meta, body = split_front_matter(path.read_text(encoding="utf-8"))
        collection = str(meta.get("collection") or "")
        if collection not in COLLECTIONS or path.parent.name != collection:
            raise ValueError(f"collection {collection!r} does not match the folder {path.parent.name!r}")
        doc_id = str(meta.get("doc_id") or "")
        if path.name not in (f"{doc_id}.md", f"{doc_id}.v{meta.get('version')}.md"):
            raise ValueError(f"doc_id {doc_id!r} does not match the file name")
        acl = _str_list(meta, "acl_groups")
        if not acl:
            raise ValueError("acl_groups is empty: nobody could read the document")
        status = str(meta.get("status") or "current")
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        dept = meta.get("dept")
        label = Label(
            level=_level(meta.get("level")),
            dept=Dept(dept) if dept else None,
            owner_id=str(meta["owner_id"]) if meta.get("owner_id") else None,
            acl_groups=acl,
            trust=Trust(str(meta.get("trust") or "internal")),
            subject_ids=_str_list(meta, "subject_ids"),
        )
        version = meta.get("version")
        return DocIn(
            doc_id=doc_id, title=str(meta.get("title") or doc_id), text=body, collection=collection,
            source=str(meta.get("source") or ""), label=label,
            version=int(version) if version is not None else None, status=status,
            source_uri=str(meta.get("source_uri") or ""),
            project_id=str(meta["project_id"]) if meta.get("project_id") else None,
            matter_id=str(meta["matter_id"]) if meta.get("matter_id") else None,
        )
    except (OSError, ValueError, TypeError, yaml.YAMLError) as e:
        # A broken file aborts the run: silently skipping it would leave a stale or missing document.
        raise IngestError(f"{path}: {e}") from None


def load_corpus(corpus_dir: Path | str) -> list[DocIn]:
    """Every document file, ordered by (doc_id, version) so older versions are ingested first."""
    docs_dir = Path(corpus_dir) / "docs"
    if not docs_dir.is_dir():
        raise IngestError(f"no corpus at {docs_dir}; run `python -m corpai synth` first")
    docs = [doc_from_file(p) for p in sorted(docs_dir.glob("*/*.md"))]
    return sorted(docs, key=lambda d: (d.doc_id, d.version or 0))
