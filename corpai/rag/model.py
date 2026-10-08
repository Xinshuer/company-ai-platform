"""Value types of the RAG package (architecture §10)."""

from __future__ import annotations

from dataclasses import dataclass, field

from corpai.policy import RagFilter
from corpai.types import Label, Level, Trust

COLLECTIONS: frozenset[str] = frozenset({
    "public", "general", "eng", "sales", "legal", "legal_regs", "finance", "hr", "exec", "playbooks",
})
# Collections whose reviewed documents are published content: money amounts there are public facts
# (classify_text(public=True), decisions D5 / issues-log X-01). Names, secrets and canaries still count.
PUBLIC_COLLECTIONS: frozenset[str] = frozenset({"public", "legal_regs"})
STATUSES: tuple[str, ...] = ("current", "superseded", "draft")


class IngestError(RuntimeError):
    """Ingestion refused to run or found a malformed document. Nothing of the failing run is committed."""


@dataclass(frozen=True)
class DocIn:
    """One document version to ingest. `label` is the label its producer assigned (synth front matter);
    ingestion only ever raises its level (rule 4), never lowers it."""

    doc_id: str
    title: str
    text: str
    collection: str
    source: str
    label: Label
    version: int | None = None  # a hint; a taken version number with different content gets max + 1
    status: str = "current"
    source_uri: str = ""
    project_id: str | None = None
    matter_id: str | None = None


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    version: int
    title: str
    heading: str
    text: str
    level: Level
    trust: Trust
    collection: str
    score: float
    source_uri: str
    subject_ids: tuple[str, ...] = ()  # the document's GDPR data subjects: recorded on the trace span, not shown


@dataclass(frozen=True)
class RetrievalResult:
    hits: list[Hit]
    level: Level  # max hit level; L1 when there are no hits
    filter: RagFilter
    collections: frozenset[str] = field(default_factory=frozenset)  # what was actually searched
