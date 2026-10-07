"""corpai.synth.run(): generate the whole company deterministically and write it.

Order matters: people → CRM → eng → legal → HR → finance (needs payroll and accounts) → corpus
(needs everything) → web → planted (recomputes the eval answers from what was generated).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corpai.config import settings
from corpai.db import pg
from corpai.synth import corpus, crm, eng, fin, hr, legal, people, planted, web
from corpai.synth._util import World
from corpai.synth.writer import write_corpus, write_db


@dataclass(frozen=True)
class SynthReport:
    seed: int
    small: bool
    counts: dict[str, int]  # table -> rows written
    planted: list[dict[str, Any]]  # [{"kind", "ref"}]; the expected values live in synth.planted (owner only)
    canaries: list[dict[str, Any]]  # [{"canary", "location", "level"}]
    corpus_dir: Path
    doc_paths: list[str] = field(default_factory=list)  # relative to corpus_dir, e.g. "docs/eng/eng-P-01-readme.md"
    web_pages_path: Path | None = None

    @property
    def docs(self) -> int:
        return len(self.doc_paths)

    def summary(self) -> str:
        rows = sum(self.counts.values())
        return (f"synth seed={self.seed} small={self.small}: {rows} rows in {len(self.counts)} tables, "
                f"{self.docs} docs, {len(self.planted)} planted issues, "
                f"{len({c['canary'] for c in self.canaries})} canaries -> {self.corpus_dir}")


def generate(seed: int = 7, small: bool = False) -> World:
    """Pure generation (no I/O). Same seed and size → identical World."""
    world = World(seed=seed, small=small)
    for mod in (people, crm, eng, legal, hr, fin, corpus, web, planted):
        mod.build(world)
    return world


def run(seed: int = 7, small: bool = False) -> SynthReport:
    world = generate(seed, small)
    with pg.connect_owner() as conn:
        counts = write_db(conn, world)
    corpus_dir = settings().corpus_dir
    paths, web_path = write_corpus(corpus_dir, world)
    report = SynthReport(seed=seed, small=small, counts=counts,
                         planted=[{"kind": p["kind"], "ref": p["ref"]} for p in world.planted],
                         canaries=[dict(c) for c in world.tables["synth.canaries"]], corpus_dir=corpus_dir,
                         doc_paths=paths, web_pages_path=web_path)
    _audit(report)
    return report


def _audit(report: SynthReport) -> None:
    # Owner jobs are audited (ENGINEERING.md). Stream A1 lands in parallel, so tolerate its absence.
    try:
        from corpai.audit import record_owner_job
    except ImportError:
        return
    record_owner_job("synth", {"seed": report.seed, "small": report.small, "rows": sum(report.counts.values()),
                               "tables": len(report.counts), "docs": report.docs,
                               "planted": len(report.planted)})
