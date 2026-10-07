"""python -m corpai serve [--host 127.0.0.1] [--port 8000]

API + MCP servers + UI in one process. Runs uvicorn on a SelectorEventLoop: psycopg's async mode
does not work on the Proactor loop uvicorn would pick on Windows (issues-log C-01), so never start
the app with the bare uvicorn CLI there.

Demo login (CORPAI_DEMO_LOGIN=1, the default) lets anyone pick any persona, the CEO included, with
no credential, and the session cookie is not Secure (plain http). That is only acceptable on the
loopback interface: serve refuses a non-loopback --host while demo login is on, unless
--allow-network-demo-login says the operator really wants every machine on the network to be able
to log in as anyone (then it warns loudly).
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import sys

log = logging.getLogger(__name__)


def is_loopback(host: str) -> bool:
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False  # any other host name; wildcards such as 0.0.0.0 and :: are not loopback either


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpai serve", description="Serve the API, MCP servers and UI.")
    ap.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (local only)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--allow-network-demo-login", action="store_true",
                    help="serve a non-loopback host although demo login (log in as anyone) is on")
    args = ap.parse_args(argv)

    from corpai.config import settings

    if not is_loopback(args.host) and settings().demo_login:
        if not args.allow_network_demo_login:
            print(f"Refusing to serve on {args.host}: demo login is on (CORPAI_DEMO_LOGIN=1), so anyone who can "
                  "reach the server could log in as any persona, the CEO included. Serve on 127.0.0.1, set "
                  "CORPAI_DEMO_LOGIN=0, or pass --allow-network-demo-login if you really mean it.", file=sys.stderr)
            return 2
        msg = (f"WARNING: serving on {args.host} with demo login ON: anyone on the network can log in as any "
               "persona without a password, over plain http.")
        print(msg, file=sys.stderr)
        log.warning(msg)

    import uvicorn

    from corpai.api import create_app

    config = uvicorn.Config(create_app(), host=args.host, port=args.port, loop="none", lifespan="on",
                            log_level="info")
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)
    return 0
