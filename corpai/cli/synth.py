"""python -m corpai synth [--seed N] [--small] [--write-company-doc]

Starts (or attaches to) the embedded Postgres, applies migrations, regenerates the fictional
company (tables + corpus) and prints a summary. --write-company-doc only rewrites docs/company.md.
"""

from __future__ import annotations

import argparse

from corpai.config import ROOT


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpai synth", description="Generate the synthetic company.")
    ap.add_argument("--seed", type=int, default=7, help="random seed (default 7); same seed = same company")
    ap.add_argument("--small", action="store_true", help="fewer filler rows; keeps personas, planted issues, canaries")
    ap.add_argument("--write-company-doc", action="store_true", help="write docs/company.md from the script and exit")
    args = ap.parse_args(argv)

    if args.write_company_doc:
        from corpai.synth.script import render_markdown

        path = ROOT / "docs" / "company.md"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_markdown())
        print(f"wrote {path}")
        return 0

    from corpai import synth
    from corpai.db import pg
    from corpai.db.migrate import apply_migrations

    pg.ensure_server()
    with pg.connect_owner() as conn:
        apply_migrations(conn)
    report = synth.run(seed=args.seed, small=args.small)
    print(report.summary())
    for table, n in report.counts.items():
        print(f"  {table:<28} {n:>6}")
    print("  planted: " + ", ".join(f"{p['kind']}({p['ref']})" for p in report.planted))
    return 0
