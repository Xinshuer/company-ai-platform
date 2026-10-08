"""python -m corpai demo-record [--only report,legal,sales,hr,qa] [--small] [--daily-tokens N] [--out-dir DIR]

Records the demo fixtures (ui/fixtures/*.js) from real runs, so the page opened from disk shows what
the platform really answers. It starts its own Postgres under .data/record/<pid> (never the dev
database), generates the company (seed 7; full size unless --small), ingests the corpus, refreshes the
metrics (2026-Q3 and 2026-09; the CFO signs and publishes 2026-Q3, 2026-09 stays draft for the board),
then drives the real API in-process (create_app + ASGI client: demo login, POST /api/runs, the
milestone routes, traces, proposal boxes) as each persona, exactly as the page would, and writes:

    report   ui/fixtures/report.js   the management report of each management persona, in Chinese and
                                     English, plus the metric board rows with their ACLs
    legal    ui/fixtures/legal.js    reviews, the draft, the due list and the send of legal staff; the
                                     head of legal's and the CEO's review and due list
    sales    ui/fixtures/sales.js    account profiles of sales staff, the head of sales and the CEO; one
                                     follow-up draft waiting for four-eyes approval
    hr       ui/fixtures/hr.js       headcount notes, screenings, the hiring managers' candidate views
    qa       ui/fixtures/runs.js, traces.js, audit.js   the shared Q&A samples (compare.js question),
                                     a local_down drill, their traces and the audit excerpt

The model mode comes from CORPAI_LLM: `live` calls DeepSeek (config/models.yaml, costs money; the
command refuses to start without the key and never prints it), `fake` uses the offline FakeLLM (tests,
dry runs; the key is removed from the process). The embedder defaults to fake (CORPAI_EMBED).
--daily-tokens raises the per-department daily token budget in the throwaway database only.
--out-dir writes the files elsewhere (tests). The database is stopped and deleted at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from corpai.config import ROOT

APPS = ("report", "legal", "sales", "hr", "qa")
FIXTURES = ROOT / "ui" / "fixtures"

REPORT_PERSONAS = ("ceo", "eng_head", "finance_head", "sales_head", "legal_head", "hr_head")
REPORT_INPUTS = {"zh": "2026-Q3 管理层报告", "en": "2026-Q3 management report"}
SIGNED, DRAFT = "2026-Q3", "2026-09"

DRAFT_REQUEST = {"mode": "draft", "template_type": "NDA", "account_id": "A-0003",
                 "variables": {"customer_name": "Rocheval Transports S.A.S.", "customer_city": "Lyon"},
                 "instructions": "Start on 2026-11-01 for 24 months."}
SEND_NOTE = "Please find our revised MSA attached; clause 9 stays as in our version."
DUE_REQUEST = {"mode": "due", "month": "2026-11"}
# persona -> contracts reviewed; legal staff also drafts, sends (four eyes) and lists the due contracts.
# The head of legal's is one legal staff cannot see. Not C-0006: it holds a canary (synth/legal.py), and a
# review returns the contract text to its reader, so the fixture would carry it.
LEGAL_REVIEWS = {"legal_staff": ("C-0004", "C-0005"), "legal_head": ("C-0007",), "ceo": ("C-0001",)}
# A live review makes 30-40 model calls and its trace holds every prompt (about 300 KiB): only the review
# with the embedded instruction keeps its trace in the fixture.
LEGAL_TRACED = (("legal_staff", "C-0004"),)

# The head of sales profiles Elbstrom, whose CRM activity sales staff may not see. No A-0001 (Nordhafen):
# its first CRM interaction holds a canary.
SALES_PROFILES = {"sales_staff": ("Havenmeer Distributie B.V.", "Elbstrom Paketdienst GmbH",
                                  "Portaluce Trasporti S.r.l."),
                  "ceo": ("Elbstrom Paketdienst GmbH", "Alpenkamm Baustofflogistik GmbH", "Rocheval Transports S.A.S."),
                  "sales_head": ("Elbstrom Paketdienst GmbH", "Rutamar Logistica S.L.")}
FOLLOWUP_AFTER = ("sales_staff", "Havenmeer Distributie B.V.")  # the follow-up is drafted in this profile's thread

HEADCOUNT_INPUT = "当前编制和招聘职位"
HEADCOUNT_PERSONAS = ("ceo", "sales_head", "eng_head", "legal_head", "finance_head", "hr_head", "hr_staff")
SCREENINGS = {"hr_staff": ("CAND-001", "CAND-002", "CAND-003", "CAND-004", "CAND-005", "CAND-006", "CAND-007",
                           "CAND-009"),
              "hr_head": ("CAND-001", "CAND-007", "CAND-013")}
HR_APPS = ("hr_candidates", "hr_screening")

QA_PUBLIC = "FleetPilot 3.0 有哪些新功能？"
QA_COMPARE = "Havenmeer 的合同什么时候续约？我们需要提前多久通知？"  # fixtures/compare.js question
QA_PROJECT = "P-02 为什么一直卡在测试阶段？"
# (persona key in the fixture, persona that runs it, input, outage switch on during the run)
# A "*" sample is served to every persona (ui/js/demo.js), so it is run as the platform admin, whose Q&A
# searches only the company-wide collections and holds only the docs tools (policy/rules.py
# OPS_ROLE_COLLECTIONS), and _check_shared refuses it if it read anything else (issues-log SEC-1).
QA_ITEMS = (("*", "platform_admin", QA_PUBLIC, None), ("sales_staff", "sales_staff", QA_COMPARE, None),
            ("sales_staff", "sales_staff", QA_COMPARE, "local_down"), ("eng_staff", "eng_staff", QA_COMPARE, None),
            ("ceo", "ceo", QA_COMPARE, None), ("eng_staff", "eng_staff", QA_PROJECT, None))
# sales staff's answer, its local_down drill and the CEO's; never a "*" sample (a trace is its runner's own)
QA_TRACED = {1, 2, 4}
SHARED_COLLECTIONS = frozenset({"public", "general"})  # in every persona's Q&A scope, all with acl ["all"]
SHARED_TOOL_DOMAINS = frozenset({"docs"})  # the Q&A tools every person holds (rule tool-docs-humans)
AUDIT_EVENTS = ("demo_login", "outage_switch", "audit_read")

# runs whose error code is transient are tried once more
RETRY_CODES = ("backend_error", "gateway_busy", "tool_unavailable")


class RecordError(RuntimeError):
    """A scenario did not produce what the demo needs; nothing of its app is written."""


# ---- setup ------------------------------------------------------------------------------------------

def _key_envs() -> list[str]:
    from corpai.gateway import load_config

    return sorted({cfg.api_key_env for cfg in load_config().routes.values()})


def _prepare(small: bool, apps: tuple[str, ...], daily_tokens: int | None) -> dict:
    """Fresh database: migrations, the company, the corpus, metric snapshots. Returns the principals."""
    from corpai import synth
    from corpai.config import settings
    from corpai.db import pg
    from corpai.db.migrate import apply_migrations
    from corpai.identity import directory
    from corpai.metrics import refresh_snapshots
    from corpai.rag import ingest_corpus
    from corpai.synth.personas import PERSONAS

    pg.ensure_server()
    with pg.connect_owner() as conn:
        apply_migrations(conn)
        if daily_tokens is not None:
            # this throwaway database only; the migration's defaults stay as they are
            conn.execute("UPDATE ops.budget_defaults SET token_limit = %s", (daily_tokens,))
    synth.run(seed=7, small=small)
    ingest_corpus(settings().corpus_dir)
    if "report" in apps:
        refresh_snapshots(SIGNED)
        refresh_snapshots(DRAFT)
    return {k: directory.get_principal_sync(uid) for k, uid in PERSONAS.items()}


async def _sign_and_publish(cfo: Any) -> None:
    from corpai.metrics import load_metrics, publish, sign_off

    ids = list(load_metrics())
    await sign_off(cfo, SIGNED, ids)
    await publish(cfo, SIGNED, ids)


def _owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    from corpai.db import pg

    with pg.connect_owner() as c:
        return c.execute(sql, params).fetchall()


# ---- the API client ---------------------------------------------------------------------------------

class Api:
    """The real API in-process, logged in as one persona at a time (demo login, session cookie)."""

    def __init__(self, client: Any, people: dict):
        self.client, self.people, self.who = client, people, None

    async def login(self, key: str) -> None:
        if self.who == key:
            return
        self.client.cookies.clear()
        r = await self.client.post("/api/auth/demo-login", json={"user_id": self.people[key].user_id})
        if r.status_code != 200:
            raise RecordError(f"demo login as {key} failed: {r.status_code}")
        self.who = key

    async def get(self, key: str, path: str) -> Any:
        await self.login(key)
        r = await self.client.get(path)
        if r.status_code != 200:
            raise RecordError(f"GET {path} as {key}: {r.status_code} {r.text[:200]}")
        return r.json()

    async def post(self, key: str, path: str, body: dict) -> Any:
        await self.login(key)
        r = await self.client.post(path, json=body)
        if r.status_code != 200:
            raise RecordError(f"POST {path} as {key}: {r.status_code} {r.text[:200]}")
        return r.json()

    async def run(self, key: str, app: str, text: str, *, thread_id: str | None = None,
                  expect: tuple[str, ...] = ("done", "waiting_approval")) -> dict:
        body = {"app": app, "input": text, "thread_id": thread_id, "user_level": 1}
        for attempt in (1, 2):
            run = await self.post(key, "/api/runs", body)
            code = (run.get("error") or {}).get("code")
            if run["status"] in expect:
                print(f"  {app:<14} {key:<13} {run['status']:<16} route={run['route']} hwm={run['hwm']}  {text[:60]}",
                      flush=True)
                return run
            if attempt == 1 and code in RETRY_CODES and thread_id is None:
                print(f"  {app} as {key}: {code}, trying once more")
                continue
            raise RecordError(f"{app} as {key} ({text[:60]!r}) ended {run['status']} {code}")
        raise AssertionError("unreachable")

    async def trace(self, key: str, run_id: str) -> dict:
        return await self.get(key, f"/api/runs/{run_id}/trace")

    async def boxes(self, keys: tuple[str, ...], agent: str) -> dict:
        """The proposal boxes of `keys`, only this agent's proposals; personas with none are left out."""
        out = {}
        for key in keys:
            me = await self.get(key, "/api/me")
            box = {"mine": [p for p in await self.get(key, "/api/proposals?box=mine") if p["agent"] == agent],
                   "to_approve": []}
            if me["can"]["approve"]:
                box["to_approve"] = [p for p in await self.get(key, "/api/proposals?box=to_approve")
                                     if p["agent"] == agent]
            if box["mine"] or box["to_approve"]:
                out[key] = box
        return out

    async def switch(self, name: str, on: bool) -> None:
        await self.post("platform_admin", "/api/admin/switches",
                        {"name": name, "on": on, "reason": "demo recording: outage drill"})


def _item(key: str, app: str, text: str, run: dict, **extra: Any) -> dict:
    return {"persona_key": key, "app": app, "input": text, **extra, "run": run}


# ---- report ------------------------------------------------------------------------------------------

def _board(period: str) -> dict:
    from corpai.db import pg
    from corpai.metrics import MetricValue, format_value, load_metrics, table_key
    from corpai.types import Level

    defs = load_metrics()
    with pg.connect_owner() as c:
        rows = c.execute(
            "SELECT DISTINCT ON (metric_id, dims_key) * FROM metrics.snapshots WHERE period = %s "
            "ORDER BY metric_id, dims_key, version DESC", (period,)).fetchall()
    out = []
    for r in rows:
        md = defs[r["metric_id"]]
        mv = MetricValue(metric_id=r["metric_id"], period=period, dims=dict(r["dims"] or {}),
                         value=None if r["value"] is None else Decimal(r["value"]), unit=r["unit"],
                         status=r["status"], level=Level(int(md.level)), suppressed=bool(r["suppressed"]),
                         name_zh=md.name_zh, name_en=md.name_en)
        draft_acl = list(r["acl_groups"]) if r["status"] in ("draft", "signed") else list(md.draft_acl)

        def iso(v):
            return v.isoformat() if v is not None else None

        # names, formula and sources are in "defs" once; a row carries what is its own
        out.append({
            "key": table_key(mv.metric_id, mv.dims), "metric_id": mv.metric_id, "dims": dict(mv.dims),
            "value": None if mv.value is None or mv.suppressed else str(mv.value), "unit": mv.unit,
            "status": r["status"], "level": int(md.level), "suppressed": mv.suppressed,
            "display_zh": format_value(mv, "zh"), "display_en": format_value(mv, "en"),
            "provenance": {"sql_sha256": r["sql_sha256"], "version": int(r["version"]), "signed_by": r["signed_by"],
                           "signed_at": iso(r["signed_at"]), "published_at": iso(r["published_at"])},
            "acl_draft": draft_acl, "acl_published": list(r["published_acl"] or []),
        })
    return {"rows": out}


async def record_report(api: Api, people: dict) -> dict:
    from corpai.metrics import load_metrics

    runs, fallback = [], []
    for key in REPORT_PERSONAS:
        for lang, text in REPORT_INPUTS.items():
            run = await api.run(key, "report", text, expect=("done",))
            data = run["output"]["data"]
            if data.get("lang") != lang or data.get("period") != SIGNED:
                raise RecordError(f"report as {key}: expected {SIGNED}/{lang}, "
                                  f"got {data.get('period')}/{data.get('lang')}")
            if data.get("used_fallback"):
                fallback.append(f"{key}/{lang} problems={data.get('problems')} attempts={data.get('attempts')}")
            runs.append(_item(key, "report", text, run))
    for line in fallback:
        print(f"  NOTE: the report writer's text failed verification, code's template was used: {line}")
    defs = [{**md.public_dict(), "draft_acl": list(md.draft_acl)} for md in load_metrics().values()]
    for d in defs:
        d.pop("sql_sha256", None)  # the hash travels with each row's provenance
    return {"runs": runs, "traces": {}, "proposals": {"by_persona": {}, "resumed_runs": {}},
            "metrics": {"defs": defs, "periods": {SIGNED: _board(SIGNED), DRAFT: _board(DRAFT)},
                        "groups": {k: sorted(p.groups) for k, p in people.items()}}}


# ---- legal -------------------------------------------------------------------------------------------

async def record_legal(api: Api, people: dict) -> dict:
    # the counterparty's first contact, as the page offers it (the address depends on the generated data)
    to = _owner_rows("SELECT email FROM crm.contacts WHERE account_id = 'A-0003' "
                     "ORDER BY contact_id LIMIT 1")[0]["email"]
    plan: list[tuple[str, dict]] = []
    for key, contracts in LEGAL_REVIEWS.items():
        plan += [(key, {"mode": "review", "contract_id": c}) for c in contracts]
        if key == "legal_staff":
            plan += [(key, DRAFT_REQUEST), (key, DUE_REQUEST),
                     (key, {"mode": "send", "contract_id": "C-0004", "to": to, "note": SEND_NOTE})]
        else:
            plan.append((key, DUE_REQUEST))
    runs, no_model, traces = [], [], {}
    for key, request in plan:
        text = json.dumps(request, ensure_ascii=False)
        run = await api.run(key, "legal_review", text)
        item = _item(key, "legal_review", text, run)
        # due lists and sends make no model call: the live API reports them with route null
        (no_model if run["route"] is None else runs).append(item)
        if (key, request.get("contract_id")) in LEGAL_TRACED and request["mode"] == "review":
            traces[run["run_id"]] = await api.trace(key, run["run_id"])
    keys = tuple(LEGAL_REVIEWS)
    contracts = {k: (await api.get(k, "/api/legal/contracts"))["contracts"] for k in keys}
    reviews = {k: (await api.get(k, "/api/legal/reviews"))["reviews"] for k in keys}
    return {"runs": runs, "no_model_runs": no_model, "traces": traces,
            "proposals": {"by_persona": await api.boxes(keys, "legal_review"), "resumed_runs": {}},
            "contracts": contracts, "reviews": reviews}


# ---- sales -------------------------------------------------------------------------------------------

async def record_sales(api: Api, people: dict) -> dict:
    runs, traces = [], {}
    for key, companies in SALES_PROFILES.items():
        for i, company in enumerate(companies):
            run = await api.run(key, "sales_profile", company)
            runs.append(_item(key, "sales_profile", company, run))
            if i == 0:
                traces[run["run_id"]] = await api.trace(key, run["run_id"])
            if (key, company) == FOLLOWUP_AFTER:
                data = run["output"]["data"]
                if not (data.get("followup") or {}).get("available") or not data.get("contacts"):
                    raise RecordError(f"no follow-up possible after {company}")
                text = f"/followup {data['contacts'][0]['contact_id']}"
                follow = await api.run(key, "sales_profile", text, thread_id=run["thread_id"],
                                       expect=("waiting_approval",))
                runs.append(_item(key, "sales_profile", text, follow, after=company))
                traces[follow["run_id"]] = await api.trace(key, follow["run_id"])
    keys = tuple(SALES_PROFILES)
    accounts = {k: (await api.get(k, "/api/sales/accounts"))["accounts"] for k in keys}
    return {"runs": runs, "traces": traces,
            "proposals": {"by_persona": await api.boxes(keys, "sales_profile"), "resumed_runs": {}},
            "accounts": {"by_persona": accounts}}


# ---- hr ----------------------------------------------------------------------------------------------

async def record_hr(api: Api, people: dict) -> dict:
    from corpai.synth.personas import PERSONAS

    runs, traces = [], {}
    for key in HEADCOUNT_PERSONAS:
        run = await api.run(key, "hr_headcount", HEADCOUNT_INPUT, expect=("done",))
        runs.append(_item(key, "hr_headcount", HEADCOUNT_INPUT, run))
        if key in ("hr_head", "hr_staff"):
            traces[run["run_id"]] = await api.trace(key, run["run_id"])
    for key, cands in SCREENINGS.items():
        for cid in cands:
            run = await api.run(key, "hr_screening", cid, expect=("done",))
            runs.append(_item(key, "hr_screening", cid, run))
            if (key, cid) == ("hr_staff", "CAND-007"):
                traces[run["run_id"]] = await api.trace(key, run["run_id"])
    # each persona's own view of the candidates, per app it may start (RLS and the hiring relation)
    candidates: dict[str, dict] = {app: {} for app in HR_APPS}
    access: dict[str, dict] = {app: {} for app in HR_APPS}
    for key in PERSONAS:
        apps = {a["id"] for a in (await api.get(key, "/api/me"))["apps"]}
        for app in HR_APPS:
            if app in apps:
                view = await api.get(key, f"/api/hr/candidates?app={app}")
                candidates[app][key] = view
                access[app][key] = [c["candidate_id"] for r in view["requisitions"] for c in r["candidates"]]
    items: dict[str, Any] = {}
    for app in HR_APPS:
        for key, ids in access[app].items():
            for cid in ids:
                if cid not in items:
                    items[cid] = await api.get(key, f"/api/hr/candidates/{cid}?app={app}")
    return {"runs": runs, "traces": traces, "proposals": {"by_persona": {}, "resumed_runs": {}},
            "candidates": candidates, "resumes": {"items": items, "access": access}}


# ---- qa (shared samples) -----------------------------------------------------------------------------

def _check_shared(run: dict) -> None:
    """A "*" sample may hold only what every persona may read: refused when it searched a collection, called
    a tool, or retrieved, used or cited a document beyond the company-wide ones (acl_groups with "all")."""
    rid = run["run_id"]
    for e in run["events"]:
        if e["t"] == "retrieval" and not set(e.get("collections") or ()) <= SHARED_COLLECTIONS:
            raise RecordError(f"shared sample {rid} searched {sorted(e['collections'])}")
        if e["t"] == "tool" and e["domain"] not in SHARED_TOOL_DOMAINS:
            raise RecordError(f"shared sample {rid} called {e['domain']}.{e['tool']}")
    data = run["output"].get("data") or {}
    sources = [s for t in data.get("tools") or [] for s in t.get("sources") or []]
    if any(not s.startswith(("rag.chunk:", "doc:")) for s in sources):
        raise RecordError(f"shared sample {rid} holds a record source: {sources}")
    chunks = [s.removeprefix("rag.chunk:") for s in sources if s.startswith("rag.chunk:")]
    docs = {c["doc_id"] for c in run["output"].get("citations") or []}
    docs |= {s.removeprefix("doc:") for s in sources if s.startswith("doc:")}  # docs.get_doc
    # every hit retrieval returned in this run, used or not, and every document a tool read: the audit
    # rows list them
    for r in _owner_rows("SELECT event, resource, details->'doc_ids' AS ids FROM audit.log WHERE run_id = %s "
                         "AND event IN ('retrieval', 'tool_call')", (rid,)):
        if r["event"] == "retrieval":
            docs |= set(r["ids"] or [])
        elif (r["resource"] or "").startswith("doc:"):
            docs.add(r["resource"].removeprefix("doc:"))
    shared = _owner_rows("SELECT DISTINCT doc_id FROM rag.documents WHERE doc_id = ANY(%s) AND 'all' = ANY(acl_groups) "
                         "UNION SELECT DISTINCT doc_id FROM rag.chunks WHERE chunk_id = ANY(%s) "
                         "AND 'all' = ANY(acl_groups)", (sorted(docs), chunks))
    ok = {r["doc_id"] for r in shared}
    bad = sorted((docs - ok) | {c for c in chunks if c.split("@", 1)[0] not in ok})
    if bad:
        raise RecordError(f"shared sample {rid} read documents not every persona may: {bad}")


async def record_qa(api: Api, people: dict) -> dict[str, dict]:
    start = int(_owner_rows("SELECT coalesce(max(seq), 0) AS n FROM audit.log")[0]["n"])
    items, traces, run_ids = [], {}, set()
    for i, (key, who, text, switch) in enumerate(QA_ITEMS):
        if switch:
            await api.switch(switch, True)
        try:
            run = await api.run(who, "qa", text, expect=("error",) if switch == "local_down" else ("done",))
        finally:
            if switch:
                await api.switch(switch, False)
        if switch == "local_down" and (run["error"] or {}).get("code") != "local_unavailable":
            raise RecordError(f"the local_down drill ended {run['error']}, not local_unavailable")
        if key == "*":
            _check_shared(run)
        run_ids.add(run["run_id"])
        items.append(_item(key, "qa", text, run, **({"switches": {switch: True}} if switch else {})))
        if i in QA_TRACED and key != "*":
            traces[run["run_id"]] = await api.trace(who, run["run_id"])
    # the audit excerpt: what the auditor reads about these runs, the drill and the read itself
    log = await api.get("auditor", f"/api/audit?after_seq={start}&limit=1000")
    keep = [e for e in log["entries"] if e["run_id"] in run_ids or e["event"] in AUDIT_EVENTS]
    entries = [{k: v for k, v in e.items() if k not in ("prev_hash", "hash")} for e in keep]
    return {"runs": {"items": items}, "traces": {"by_run": traces},
            "audit": {"entries": entries, "chain": log["chain"]}}


# ---- writing -----------------------------------------------------------------------------------------

def _text(name: str, data: dict, *, compact: bool, live: bool) -> str:
    """The fixture as JSON, refused when it would carry a canary or, in a live recording, fake-model text."""
    if compact:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=1)
    if "KB-CANARY-" in text:
        raise RecordError(f"a canary reached {name}.js; refusing to write it")
    if live and "[fake/" in text:
        raise RecordError(f"fake-model text in a live recording of {name}.js; refusing to write it")
    return text


def _save(out_dir: Path, name: str, text: str) -> None:
    path = out_dir / f"{name}.js"
    path.write_text(f"window.FIXTURES.{name} = {text};\n", encoding="utf-8", newline="\n")
    print(f"wrote {path} ({len(text) // 1024} KiB)", flush=True)


def _usage() -> dict:
    """Model calls and tokens of this recording, from the gateway's audit rows (metadata only)."""
    rows = _owner_rows(
        "SELECT route, count(*) AS calls, coalesce(sum((details->>'prompt_tokens')::bigint), 0) AS prompt, "
        "coalesce(sum((details->>'completion_tokens')::bigint), 0) AS completion, "
        "coalesce(sum((details->>'total_tokens')::bigint), 0) AS total "
        "FROM audit.log WHERE event = 'model_call' AND decision = 'allow' GROUP BY route ORDER BY route")
    budget = _owner_rows("SELECT coalesce(sum(tokens_used), 0) AS n FROM ops.budgets")[0]["n"]
    return {"by_route": {r["route"]: {k: int(r[k]) for k in ("calls", "prompt", "completion", "total")} for r in rows},
            "calls": sum(int(r["calls"]) for r in rows), "tokens": sum(int(r["total"]) for r in rows),
            "budget_tokens_used": int(budget)}


async def _record(apps: tuple[str, ...], people: dict, out_dir: Path, meta: dict) -> str | None:
    """Record and write each app in turn; the first app that fails stops the recording (its files and
    those of the apps after it are left as they were). Returns that failure, or None."""
    import httpx2

    from corpai.api.app import create_app

    live = meta["mode"] == "live"
    if "report" in apps:
        await _sign_and_publish(people["finance_head"])
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            api = Api(client, people)
            for name in apps:
                print(f"recording {name} ...", flush=True)
                # caught here: raised through the app's lifespan, it would come out as an exception group
                try:
                    fixture = await RECORDERS[name](api, people)
                    files = ({"runs": {"recorded": meta, **fixture["runs"]}, "traces": fixture["traces"],
                              "audit": fixture["audit"]} if name == "qa" else {name: {"recorded": meta, **fixture}})
                    texts = {f: _text(f, data, compact=f == "report", live=live) for f, data in files.items()}
                except RecordError as e:
                    return f"{name}: {e}"
                for f, text in texts.items():
                    _save(out_dir, f, text)
    return None


RECORDERS = {"report": record_report, "legal": record_legal, "sales": record_sales, "hr": record_hr,
             "qa": record_qa}


def _apps(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return APPS
    names = [s.strip() for s in raw.split(",") if s.strip()]
    unknown = sorted(set(names) - set(APPS))
    if unknown or not names:
        raise SystemExit(f"--only takes a comma-separated subset of {', '.join(APPS)}; unknown: {', '.join(unknown)}")
    return tuple(a for a in APPS if a in names)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m corpai demo-record",
                                 description="Record the demo fixtures (ui/fixtures/*.js) from real runs.")
    ap.add_argument("--only", help=f"comma-separated subset of {','.join(APPS)} (default: all)")
    ap.add_argument("--small", action="store_true", help="the small company (faster; the demo uses the full one)")
    ap.add_argument("--daily-tokens", type=int, default=None,
                    help="per-department daily token budget in the throwaway database (default: the migration's)")
    ap.add_argument("--out-dir", type=Path, default=FIXTURES, help="where to write the files (default ui/fixtures)")
    args = ap.parse_args(argv)
    apps = _apps(args.only)
    if args.daily_tokens is not None and args.daily_tokens < 1:
        raise SystemExit("--daily-tokens must be a positive number of tokens")

    # before any module reads the settings: own data dir, demo login on, the fake embedder by default
    data_dir = ROOT / ".data" / "record" / str(os.getpid())
    os.environ["CORPAI_DATA_DIR"] = str(data_dir)
    os.environ["CORPAI_DEMO_LOGIN"] = "1"
    os.environ.setdefault("CORPAI_EMBED", "fake")
    from corpai.config import reload_settings

    s = reload_settings()
    if s.llm_mode == "live":
        missing = [name for name in _key_envs() if not os.environ.get(name)]
        if missing:
            print(f"demo-record: CORPAI_LLM=live needs {', '.join(missing)} in the environment; it is not set. "
                  "Set it, or run with CORPAI_LLM=fake.", file=sys.stderr)
            return 2
        print("demo-record: model mode LIVE: every model call goes to the DeepSeek API (config/models.yaml) "
              "and costs money")
    else:
        for name in _key_envs():
            os.environ.pop(name, None)  # nothing in this process can reach the paid API
        print("demo-record: model mode fake: the offline FakeLLM writes the text (set CORPAI_LLM=live to record "
              "real model output)")
    from corpai.gateway import load_config
    from corpai.types import Route

    meta = {"mode": s.llm_mode, "model": load_config().routes[Route.LOCAL].model if s.llm_mode == "live" else "fake",
            "on": date.today().isoformat(), "company": "small" if args.small else "full", "seed": 7}
    print(f"demo-record: apps {', '.join(apps)}; embedder {s.embed_mode}; company {meta['company']}; "
          f"database {data_dir}; output {args.out_dir}", flush=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("httpx", "httpx2", "mcp"):  # one line per in-process request otherwise
        logging.getLogger(name).setLevel(logging.WARNING)

    from corpai.db import pg

    shutil.rmtree(data_dir, ignore_errors=True)
    try:
        people = _prepare(args.small, apps, args.daily_tokens)
        try:
            failed = asyncio.run(_record(apps, people, args.out_dir, meta), loop_factory=asyncio.SelectorEventLoop)
        finally:
            use = _usage()
            print(f"model calls: {use['calls']} ({json.dumps(use['by_route'])}); tokens {use['tokens']}; "
                  f"budget tokens used {use['budget_tokens_used']}")
    finally:
        pg.stop_server()
        shutil.rmtree(data_dir, ignore_errors=True)
    if failed:
        print(f"demo-record failed: {failed}", file=sys.stderr)
        return 1
    return 0
