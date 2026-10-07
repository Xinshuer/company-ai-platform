"""Write a generated World: wipe and refill the tables (owner role, one transaction), then the corpus.

Idempotent by construction: every run TRUNCATEs what it owns (CASCADE, because other streams add
tables that reference core.users, e.g. core.sessions) and deletes the corpus subtrees it writes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psycopg
import yaml

from corpai.synth._util import Doc, World

# Insert order respects foreign keys.
TABLES: tuple[str, ...] = (
    "core.users", "core.memberships", "core.grants",
    "crm.accounts", "crm.contacts", "crm.opportunities", "crm.interactions", "crm.quotes",
    "eng.projects", "eng.tickets", "eng.ci_runs", "eng.vulnerabilities",
    "legal.contracts", "legal.contract_requests", "legal.matters", "legal.regulations", "legal.clause_library",
    "fin.chart_of_accounts", "fin.journal_entries", "fin.invoices", "fin.budgets",
    "hr.employees", "hr.compensation", "hr.performance", "hr.requisitions", "hr.requisition_budgets",
    "hr.candidates", "hr.screenings",
    "synth.planted", "synth.canaries",
)
BUSINESS_TABLES: tuple[str, ...] = tuple(t for t in TABLES if t.split(".")[0] in ("crm", "eng", "legal", "fin", "hr"))


def _copy(conn: psycopg.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    colset = set(cols)
    for r in rows:
        if r.keys() != colset:
            raise ValueError(f"{table}: rows have different columns: {sorted(colset ^ set(r.keys()))}")
    col_sql = ", ".join(cols)
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({col_sql}) FROM STDIN") as cp:  # table/cols are our constants, not input
            for r in rows:
                cp.write_row([r[c] for c in cols])


def write_db(conn: psycopg.Connection, world: World) -> dict[str, int]:
    world.tables["core.memberships"] = [{"user_id": u, "grp": g} for u, g in sorted(world.memberships)]
    world.tables["synth.planted"] = world.planted
    world.tables["synth.canaries"] = sorted(world.canaries, key=lambda c: (c["canary"], c["location"]))
    unknown = set(world.tables) - set(TABLES)
    if unknown:
        raise ValueError(f"generator produced rows for unknown tables: {sorted(unknown)}")
    with conn.transaction():
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " CASCADE")
        for t in TABLES:
            _copy(conn, t, world.tables.get(t, []))
    return {t: len(world.tables.get(t, [])) for t in TABLES}


def render_doc(doc: Doc) -> str:
    front = yaml.safe_dump(doc.meta(), sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)
    body = doc.body if doc.body.endswith("\n") else doc.body + "\n"
    return f"---\n{front}---\n\n{body}"


def write_corpus(corpus_dir: Path, world: World) -> tuple[list[str], Path]:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("docs", "web"):  # only the subtrees we own; leave anything else in corpus_dir alone
        shutil.rmtree(corpus_dir / sub, ignore_errors=True)
    paths = []
    for doc in sorted(world.docs, key=lambda d: d.relpath):
        path = corpus_dir / doc.relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_doc(doc))
        paths.append(doc.relpath)
    web_path = corpus_dir / "web" / "pages.jsonl"
    web_path.parent.mkdir(parents=True, exist_ok=True)
    with open(web_path, "w", encoding="utf-8", newline="\n") as f:
        for page in world.web_pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")
    return paths, web_path
