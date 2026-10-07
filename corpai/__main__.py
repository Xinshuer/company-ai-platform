"""python -m corpai <command> [args]

Each command lives in corpai/cli/<command>.py (dashes become underscores) and exposes
main(argv: list[str]) -> int, so build streams add commands without touching this file.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import corpai.cli


def _commands() -> list[str]:
    return sorted(m.name.replace("_", "-") for m in pkgutil.iter_modules(corpai.cli.__path__) if not m.name.startswith("_"))


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m corpai <command> [args]\ncommands: " + ", ".join(_commands()))
        return 0
    name = argv[0].replace("-", "_")
    try:
        mod = importlib.import_module(f"corpai.cli.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"corpai.cli.{name}":
            raise
        print(f"unknown command {argv[0]!r}; commands: " + ", ".join(_commands()), file=sys.stderr)
        return 2
    return int(mod.main(argv[1:]) or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
