"""python -m corpai metrics refresh|sign|publish|list|show   (demo and operations helper)

    refresh --period 2026-09                     owner job svc:metrics: compute draft snapshots
    sign    --period 2026-09 --as finance_head [--metrics a,b | --all]
    publish --period 2026-09 --as finance_head [--metrics a,b | --all]
    list    --as sales_staff                     metric definitions + what that person can see
    show    --period 2026-09 --as ceo --metric opex_actual   every visible row

`--as` takes a persona key (demo personas) or a user id; sign and publish run as that person, so the
database refuses anyone but the head of finance. Sign and publish are audited.

`--as` is a demo login without a password, exactly like the API's demo login: it works only while
demo login is on (CORPAI_DEMO_LOGIN=1, settings().demo_login) and is refused otherwise. Every use is
audited as a `demo_login` event with action `cli_impersonation`, the OS user who ran the command and
the command, so a CLI sign-off can be told apart from the CFO signing in the app.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from corpai.metrics.numbers import format_value


class ImpersonationRefused(SystemExit):
    """--as used while demo login is off."""


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001  no login name on this host: still audit the attempt
        return "unknown"


def _principal(who: str, command: str = "", period: str | None = None):
    from corpai import audit
    from corpai.config import settings
    from corpai.identity import directory
    from corpai.synth.personas import PERSONAS

    if not settings().demo_login:
        raise ImpersonationRefused("--as is a demo login and demo login is off (CORPAI_DEMO_LOGIN=0); "
                                   "sign and publish in the app with your own login")
    user_id = PERSONAS.get(who, who)
    p = directory.get_principal_sync(user_id)
    if p is None:
        raise SystemExit(f"unknown user or persona {who!r}")
    audit.record("demo_login", actor=p.user_id, action="cli_impersonation", resource=f"cli:metrics.{command}",
                 decision="allow", reason="demo login from the metrics CLI",
                 details={"os_user": _os_user(), "command": command, "period": period, "via": "cli"})
    return p


def _run(coro):
    # psycopg async does not work on the Windows Proactor loop (ENGINEERING.md "Async and Windows").
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpai metrics", description="Metric layer (M3).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="compute draft snapshots (owner job)")
    r.add_argument("--period", required=True, help="YYYY-MM or YYYY-Qn")
    for name in ("sign", "publish"):
        s = sub.add_parser(name, help=f"{name} metrics as the head of finance")
        s.add_argument("--period", required=True)
        s.add_argument("--as", dest="who", required=True, help="persona key or user id")
        g = s.add_mutually_exclusive_group(required=True)
        g.add_argument("--metrics", help="comma-separated metric ids")
        g.add_argument("--all", action="store_true")
    ls = sub.add_parser("list", help="definitions and what a person can see")
    ls.add_argument("--as", dest="who", required=True)
    sh = sub.add_parser("show", help="every visible row of one metric")
    sh.add_argument("--period", required=True)
    sh.add_argument("--as", dest="who", required=True)
    sh.add_argument("--metric", required=True)
    sh.add_argument("--lang", choices=("zh", "en"), default="en")
    args = ap.parse_args(argv)
    try:  # zh output on a Windows console
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    from corpai.db import pg
    from corpai.db.migrate import apply_migrations
    from corpai.metrics import get_breakdown, list_metrics, load_metrics, publish, refresh_snapshots, sign_off

    pg.ensure_server()
    if args.cmd == "refresh":
        with pg.connect_owner() as conn:
            apply_migrations(conn)
        print(json.dumps(refresh_snapshots(args.period).to_dict(), indent=2))
        return 0
    p = _principal(args.who, args.cmd, getattr(args, "period", None))
    if args.cmd in ("sign", "publish"):
        ids = list(load_metrics()) if args.all else [m.strip() for m in args.metrics.split(",") if m.strip()]
        fn = sign_off if args.cmd == "sign" else publish
        try:
            res = _run(fn(p, args.period, ids))
        except PermissionError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 3
        print(json.dumps(res.to_dict(), indent=2))
        return 0
    if args.cmd == "list":
        for m in _run(list_metrics(p)):
            periods = ", ".join(f"{x['period']}({'/'.join(x['statuses'])})" for x in m["periods"]) or m["access"]
            print(f"{m['metric_id']:<32} {m['unit']:<6} {periods}")
        return 0
    rows = _run(get_breakdown(p, args.metric, args.period))
    if not rows:
        print(f"{args.metric} {args.period}: no permission or not published")
        return 0
    for mv in rows:
        dims = ",".join(f"{k}={v}" for k, v in sorted(mv.dims.items())) or "total"
        print(f"{mv.metric_id:<28} {dims:<22} {format_value(mv, args.lang):<24} {mv.status:<9} L{int(mv.level)}")
    return 0
