"""python -m corpai.mcp_servers <domain> [--host 127.0.0.1] [--port 8101]: one MCP server on its own."""

from __future__ import annotations

import argparse
import sys

from corpai.mcp_servers import DOMAINS, run_standalone


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpai.mcp_servers")
    ap.add_argument("domain", choices=DOMAINS)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8101)
    args = ap.parse_args(argv)
    run_standalone(args.domain, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
