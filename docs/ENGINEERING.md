# Engineering conventions

Read [architecture.md](architecture.md) first. This file says how code is written, tested and run.

## Environment

- Python 3.12 in `.venv` (`.venv\Scripts\python.exe`). Pinned versions: `requirements.lock.txt`. Add a dependency only if the spec needs it; then `pip install` it into `.venv` and regenerate the lock file.
- Caches stay inside the project or on G: — `HF_HOME` and fastembed `cache_dir` = `.cache/`, pip cache `G:\pip-cache`, temp `G:\Temp`. Nothing goes to `C:`.
- Run everything from the repo root. `pyproject.toml` sets `pythonpath = ["."]` for pytest.

## Commands

```text
python -m corpai db up          # start pgserver (.data/pg) and apply migrations
python -m corpai db down        # stop it (pg_ctl stop -m fast)
python -m corpai synth          # generate the company into the DB + .data/corpus
python -m corpai ingest         # load the corpus into RAG
python -m corpai serve          # API + MCP servers + UI on http://127.0.0.1:8000
python -m corpai demo-record    # (live) record UI fixtures from real runs
python -m pytest                # all tests, fake LLM, fake embedder
python -m pytest -m security    # permission matrix, routing canaries, chaos, red team
python -m pytest -m eval        # eval suites, fake model (testpaths include tests/, so this never goes live)
CORPAI_LLM=live CORPAI_EVAL_RUNS=3 python -m pytest evals/legal -m eval   # live evals: name eval paths only
```

Live evals call the real DeepSeek API and cost money. A run is live only when `CORPAI_LLM=live` **and** every path on the command line lies under `evals/` (for example `evals`, `evals/legal`, `evals/qa`). With no paths, or with any path outside `evals/`, the session stays on the fake model and the API key is removed from the process. The pytest header says which mode the session is in.

## Async and Windows

- Request paths are async: FastAPI, LangGraph `ainvoke`, `psycopg` `AsyncConnectionPool`, `AsyncPostgresSaver`, the MCP client.
- **psycopg async does not work on the Proactor event loop**, which is what uvicorn picks on Windows. `python -m corpai serve` therefore runs `asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)` with `uvicorn.Config(..., loop="none")`. Never start the app with the bare `uvicorn` CLI on Windows.
- Async tests use the anyio pytest plugin: `@pytest.mark.anyio`. `tests/conftest.py` defines `anyio_backend` as `("asyncio", {"loop_factory": asyncio.SelectorEventLoop})`.
- Scripts and jobs (migrations, synth, ingestion) are sync and use `connect_owner()`.

## Database rules

- Request-path code connects as `corpai_app` and runs every query inside `principal_tx(principal)`. A query outside it sees nothing — that is intended.
- Only migrations, synth, ingestion, metric refresh and proposal executors use the owner connection; each writes an `owner_job` audit event.
- Every new table: label columns (if it holds content), `ENABLE ROW LEVEL SECURITY` (not `FORCE`: owner jobs and `SECURITY DEFINER` functions must bypass), a policy using `core.can_read(level, acl_groups)`, and explicit `GRANT`s to `corpai_app`, all in the same migration file. No `GRANT ... ON ALL TABLES` (exception: schema `lg`, whose tables LangGraph creates at runtime).
- Use parameters (`%s`) everywhere; never format SQL with user text. Session variables via `set_config(name, value, true)`.
- Migration file names: `migrations/NNN_<stream>_<name>.sql` within your stream's number range (architecture §4).

## Security rules that code review enforces

1. Principal and level come from code (`RunContext`, state), never from model output or tool arguments. No tool takes a `user_id` argument.
2. Route is computed by the gateway from the level. Callers never pass a route.
3. Fail closed: missing label → L4; classifier error → L4; `local` down → error, never `external`.
4. Tool tokens, session tokens and API keys never appear in state, checkpoints, traces, audit details, logs or exceptions.
5. `DEEPSEEK_API_KEY` is read from the environment at call time only. Never print it, never write it to a file, never put it in a fixture.
6. Denials are explicit: an envelope lists what was denied; reports say "no permission" instead of skipping.
7. Every write goes through a proposal. Executors re-check the params hash.
8. Content from `web`, uploaded contracts and résumés is `trust=untrusted`: it may only enter nodes that have no write tools and no outbound tools.

## Tests

- `tests/conftest.py` (core, frozen) provides the fixtures below. The root `conftest.py` registers it once as a global plugin (issues-log D-11), so `evals/` uses the very same fixtures and one database, whichever order the directories are collected in (`pytest tests evals` or `pytest evals tests`). Do not copy these fixtures into `evals/*/conftest.py`; those files hold eval-only fixtures.
  - forces `CORPAI_LLM=fake`, `CORPAI_EMBED=fake`, and removes `DEEPSEEK_API_KEY` from `os.environ` for the whole run;
  - `pg` (session): a fresh pgserver under `.data/test/<pid>` (so parallel pytest runs never collide), all migrations applied, stopped with `pg_ctl` and deleted at the end;
  - `seeded` (session): `synth --small --seed 7` + ingest, once per session;
  - `personas`: dict persona key → `Principal` (keys in architecture §15, e.g. `personas["sales_staff"]`, `personas["finance_head"]`);
  - `fake_llm`: the gateway's `FakeLLM`, calls cleared per test;
  - `app_client`: `httpx2.AsyncClient` on the ASGI app with MCP servers mounted.
- Markers: `security`, `eval`, `live` (live = real DeepSeek; skipped unless `CORPAI_LLM=live`), `slow`.
- Tests that write data use fresh ids or clean up; never depend on test order.
- Table-driven tests for policy and classification (YAML or parametrised lists).
- A stream is done when its tests pass together with everyone else's: `python -m pytest -q` green.

## Style

- Type hints on public functions; `from __future__ import annotations`.
- Small modules; public API re-exported from the package `__init__.py`; private helpers prefixed `_`.
- Comments explain *why* (a security reason, a regulation, a gotcha), not what the line does.
- Data and docs in English (EU company). UI strings zh first with en. User-facing error messages are short and say what to do.
- No real people, companies or domains. Synthetic domains end in `.example` (e.g. `nordhafen-logistik.example`).

## Files nobody edits outside their owner

- Frozen core: `corpai/types.py`, `corpai/clock.py`, `corpai/config.py`, `corpai/ids.py`, `corpai/db/pg.py`, `corpai/db/migrate.py`, `corpai/__main__.py`, `corpai/cli/db.py`, `migrations/000_*`, `migrations/020_*`, `tests/conftest.py`, `tests/core/`, `pyproject.toml`. Need a change? Write it in your report; the integrator applies it.
- Another stream's package or migration range. Import its public API; if it is missing something, stub against the spec signature and report it.
- `docs/design-review.md`, `docs/decisions.md`, `docs/architecture.md` — integrator only.

## Git

- Commits by the integrator only, author `Xinshuer <190136517+Xinshuer@users.noreply.github.com>` (repo-local config), no AI attribution lines. Build agents do not commit.
- `.data/`, `.cache/`, `.venv/`, secrets never committed.
