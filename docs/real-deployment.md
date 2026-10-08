# Running it for real

**English** | [中文](real-deployment.zh.md)

What would have to change to run this platform for a real company of about 70 people in Sweden, like the fictional ABC Tech AB. Every item names the module or config file that changes today. Nothing here is implemented; the demo stays a one-process simulation ([architecture §17](architecture.md#17-if-this-were-deployed-for-real)).

Most of the design carries over unchanged: business code only knows the route names `local` and `external`, permissions are decided by code and enforced again by row-level security, every write goes through a proposal, and the audit chain is in the database. What changes is the infrastructure around it: a real local model, real identities, shared state for several workers, secrets, network controls and operations.

Figures below (hardware, prices, load) are **order-of-magnitude estimates for planning, not measurements**. Benchmark with your own prompts and get quotes before buying.

## Contents

1. [Target picture](#1-target-picture)
2. [The local route: hardware and serving](#2-the-local-route-hardware-and-serving)
3. [The external route and EU data residency](#3-the-external-route-and-eu-data-residency)
4. [Identity](#4-identity)
5. [Policy](#5-policy)
6. [Several workers: state that is per process today](#6-several-workers-state-that-is-per-process-today)
7. [Database](#7-database)
8. [Secrets](#8-secrets)
9. [Audit anchors on storage the database host cannot rewrite](#9-audit-anchors-on-storage-the-database-host-cannot-rewrite)
10. [Observability](#10-observability)
11. [Network and egress](#11-network-and-egress)
12. [Backups and retention](#12-backups-and-retention)
13. [Cost model](#13-cost-model)
14. [Rollout plan](#14-rollout-plan)
15. [Checklist of code and config changes](#15-checklist-of-code-and-config-changes)

## 1. Target picture

```text
                 IdP (Keycloak or Entra ID): OIDC login; SCIM or HR system feeds the directory sync
                                  ▲
staff browser ──TLS──► reverse proxy ──► API workers ×2–3 (corpai/api, MCP servers in-process, app role only)
                                           │          │          │
                                           │          │          └──► egress proxy ──► external model API (EU)
                                           │          └──► GPU host: vLLM or SGLang (local route, internal TLS)
                                           ▼
            Postgres 16 + pgvector (primary + standby) ◄── job runner (owner role: executors, directory sync,
                                                            metric refresh, prune, audit anchors ──► WORM bucket)
            masked spans ──► OpenTelemetry collector / self-hosted Langfuse
```

All of it runs in Sweden or elsewhere in the EU. The API workers hold only the `corpai_app` database role; owner-role work moves to a separate job runner (section 7).

## 2. The local route: hardware and serving

**Today.** `config/models.yaml` points both routes at `https://api.deepseek.com`; `local` carries `simulated_onprem: true`, and `config/egress.yaml` lists `model.local` with the same host. Switching `local` to a real server changes only its `base_url`, `model`, `api_key_env` and its egress entry, plus the points below.

**Serving.** Run an OpenAI-compatible inference server on a Linux GPU host: [vLLM](https://docs.vllm.ai/) or [SGLang](https://docs.sglang.ai/). Both do continuous batching and prefix caching, which suits this platform: the legal review sends the same playbook prompt for every clause. What the code needs from the server:

- **Tool calling.** `qa` offers read-only tools in up to 3 rounds (`corpai/agents/qa.py`, `MAX_ROUNDS`), and Clause Check's per-clause loop calls its lookups as tools (`corpai/legal/clausecheck/review.py`, up to `max_steps = 6` per clause). Start the server with the tool-call parser for the chosen model family.
- **JSON mode.** The gateway sends `response_format: {"type": "json_object"}` when `json_mode` is set (`corpai/gateway/backend.py`); the server must support structured output.
- **HTTPS.** The backend refuses non-https URLs (`corpai/gateway/egress.py`). Put the server behind TLS with a certificate from the company CA. The HTTP client verifies against the operating system's trust store (httpx2 uses `truststore`), and because it is built with `trust_env=False` (so proxy variables cannot add a hop) it ignores `SSL_CERT_FILE`: install the company CA in the API hosts' system trust store, or pass it explicitly in `corpai/gateway/backend.py`.
- **No prompt logging** on the inference host (check the server's request-logging flags), and prefix-cache isolation between users if timing side channels matter (vLLM accepts a per-request cache salt).
- **Model-specific request fields.** `extra_body: {thinking: {type: disabled}}` in `config/models.yaml` is DeepSeek's switch and is global today (`corpai/gateway/conf.py`, `GatewayConfig.extra_body`). Move it under each route, so the local server gets its own fields (for example a chat-template flag that turns thinking off).

**Model.** A mid-size open-weight instruct model, roughly 30B parameters dense (or a mixture-of-experts model of similar total size), with good English, German and Swedish, reliable tool calls and JSON, and a licence that allows commercial internal use. Pick it by running the eval suites against it, not by benchmarks: set `local` to the candidate server and run `CORPAI_LLM=live python -m pytest evals/qa evals/routing evals/legal -m eval` (the legal harness needs CORPAI_EVAL_RUNS ≥ 3 to count as validated, `evals/legal/harness.py`). With a real local route, live evals still send L1–L2 calls to whatever `external` points at.

**GPU sizing.** Memory is the constraint: weights plus KV cache.

| | Weights, ~30B dense | KV cache |
|---|---|---|
| BF16 | ~64 GB | ~0.25 MB per token for a typical 30B model with grouped-query attention: an 8k-token request holds ~2 GB |
| FP8 | ~32 GB | about half with an FP8 KV cache |
| 4-bit (AWQ/GPTQ) | ~18 GB | |

Options for 70 people:

- **One server with two 48 GB GPUs**, each running its own replica of an FP8 or 4-bit model behind a small load balancer. After weights and runtime overhead each card keeps roughly 10–25 GB for KV cache, i.e. about 5–12 concurrent 8k-token requests per replica (twice that with an FP8 KV cache). Two replicas also mean one GPU failure does not stop the local route; with one replica, a failure stops every L3/L4 feature, because the gateway never falls back to `external` (`corpai/gateway/gateway.py`, rule 4).
- **One 80–96 GB GPU** with an FP8 model: simpler, more KV cache, no redundancy.

Quantisation trades quality for memory and speed. FP8 is close to BF16 on current GPUs; 4-bit is usually fine for Q&A and drafting but worth checking on the legal eval set, where a missed high-risk clause is the costly error (`evals/legal`). Decide with the eval numbers, not on principle.

**Load and the semaphores.** Rough demand: 70 staff, perhaps 40 active on a given day, a few model calls in flight at peak. The heavy user is the legal review: clauses are reviewed one after another (`corpai/legal/review.py`, up to 120 clauses, each up to 6 model steps), so a 20-clause contract is 20–120 sequential local calls and holds one slot for minutes. `config/models.yaml` limits in-flight calls per route **per event loop** (`max_concurrency`: local 2, external 8; `asyncio.Semaphore` in `ModelGateway._sem`), and a call waits `queue_timeout_s` (local 20 s) before `GatewayBusy`. With real hardware:

- set the inference server's own limit (vLLM `--max-num-seqs`) to what keeps latency acceptable in a benchmark, then set local `max_concurrency` to that limit divided by the number of API workers (each worker has its own semaphore, section 6);
- raise local `queue_timeout_s` and `timeout_s` to fit legal reviews, or run long reviews as background jobs instead of inside the HTTP request;
- keep the per-department daily token budget (`ops.budget_defaults`, 2,000,000 tokens per department per UTC day, migration 010): it is already in the database and therefore shared by all workers. On the local route it is a fairness limit rather than a cost limit.

## 3. The external route and EU data residency

**Today** `external` is the DeepSeek API, operated from China. The gateway only sends L1–L2 there (rule 3 in [architecture §8](architecture.md#8-model-gateway-a4)); personal data is L3 by rule (decisions D2, D4) and DLP can only move a call to `local`. For a real Swedish company that is still not good enough:

- L2 is internal company information, and classification is pattern- and gazetteer-based (`corpai/classify`). A confidential fact without a detectable marker can be rated L2.
- Sending personal data to a country without an adequacy decision needs a GDPR Chapter V basis (standard contractual clauses plus a transfer impact assessment). Even if the design aims to send none, a misclassification would be such a transfer.

So in production, either point `external` at a model provider with EU data residency, a data processing agreement (GDPR Art. 28) and no training on customer data, or drop the external route: point `external` at the local server too (the routing code stays as it is; only `config/models.yaml` and `config/egress.yaml` change). The second option needs more GPU capacity, because every call then runs on the company's own hardware; the first keeps cheap provider capacity for public and internal questions, under an EU contract.

Everything else that holds L3/L4 data stays in the EU, preferably in Sweden: the database and its backups, the GPU host, trace and observability storage, the audit anchor bucket and the identity provider. The record of processing and the DPIA belong in [compliance/](../compliance/).

## 4. Identity

**Today** anyone can pick any persona: `POST /api/auth/demo-login` (`corpai/api/routes_core.py`) issues a session for a listed persona, and `python -m corpai metrics ... --as` does the same on the command line (`corpai/cli/metrics.py`). Both work only while `CORPAI_DEMO_LOGIN=1` (`corpai/config.py`) and both are audited as `demo_login`. The directory (`core.users`, `core.memberships`, `core.grants`) is written by `corpai/synth`.

**Login.** Use OpenID Connect with the authorization-code flow and PKCE against Keycloak (self-hosted, keeps identity data in-house) or Entra ID (if the company already runs Microsoft 365). The API is the OIDC client (backend-for-frontend): new routes in `corpai/api/routes_core.py` handle the redirect and callback, map the IdP subject to `core.users.user_id` (a new column for the IdP subject), and then call the existing `tokens.aissue_session(user_id)`. Everything after that stays: the HttpOnly session cookie, the `core.sessions` row, re-resolving the principal from the directory on every request (`corpai/api/security.py`, `session_principal`). Set `CORPAI_DEMO_LOGIN=0`, which turns off `/api/personas`, the demo login and the CLI `--as`. MFA is enforced at the IdP. The UI's persona picker (`ui/js/view_login.js`) becomes a "Sign in" button.

Also change for TLS: the cookie gets `secure=True` (`set_session_cookie`, `corpai/api/security.py`), and the CSP `connect-src` drops `http://127.0.0.1:8000` (`corpai/api/security.py` and the meta tag in `ui/index.html`). Set `CORPAI_ALLOWED_HOSTS` to the name staff use to reach the server: against DNS rebinding the app answers only loopback names and the configured ones (`HostCheck`, issues-log FN-26).

**Group mapping.** Do **not** take ACL groups from token claims. Claims are frozen for the lifetime of a login, while `identity.groups.effective_groups` recomputes groups from the directory on every request, so a deactivation or a revoked grant takes effect at once ([architecture §5](architecture.md#5-identity-groups-and-tokens-a3)). Instead, keep the directory tables and fill them from the systems of record with an owner job (audited as `owner_job`, like synth today):

| Directory data | Source |
|---|---|
| `core.users`: name, email, dept, role (`employee`, `manager`, `ceo`, ...), title, `manager_id`, `active` | HR system, or the IdP through SCIM provisioning; IdP groups such as `dept-sales` or `heads` map to dept and role |
| `clearance` | derived by the rule in [architecture §5.1](architecture.md#51-clearance-and-acl-assignment-the-data-contract-between-synth-policy-and-rls), not entered by hand |
| `core.memberships`: `proj:`, `matter:`, `req:` | ticket system (project members), legal matter register (handling lawyers), applicant tracking (hiring panel) |
| `core.grants` (time-boxed access, e.g. legal looking at one CRM account) | a small approval flow for platform admins with a ticket reference; today only synth writes them |

Leavers: SCIM sets `active = false`, the next request finds an inactive principal and gets nothing, and `revoke_user_sessions` ends open sessions at once.

## 5. Policy

**Today** the rules are Python tables in `corpai/policy/rules.py`, evaluated by a pure `decide()` (`corpai/policy/engine.py`), and the same read rules are enforced again by RLS policies (`core.can_read`, migrations). Privileged SQL functions repeat role checks. Tests prove that `decide()` and RLS agree for every persona (`tests/security/test_permission_matrix.py`, `test_permission_matrix_rls.py`).

**OPA (Rego) or Cedar instead of the Python tables?** Benefits: policy reviewed and deployed separately from code, decision logs, one engine if other services need the same decisions. Costs: a network hop or sidecar per decision, a second language next to SQL, and the RLS policies still have to be maintained by hand because the database cannot call OPA.

For one 70-person company with one application, keeping the Python tables is defensible. If the platform grows to several services, move `decide()`, `tool_allowed()` and `can_approve()` behind the same function signatures to OPA or Cedar and use `tests/security/permission_matrix.yaml` as the shared conformance test for both engines. **Keep RLS either way**: it is the check that still holds when application code has a bug, and the app role is `NOBYPASSRLS` (`corpai/db/pg.py`).

## 6. Several workers: state that is per process today

`python -m corpai serve` is one process (`corpai/cli/serve.py`). Running two or three workers for availability needs these changes first:

| State | Where today | Change |
|---|---|---|
| Uploaded contracts | process memory, per user, 2 h (`corpai/agents/legal/uploads.py`) | a table (e.g. `legal.uploads`) with label columns, `trust = 'untrusted'`, RLS "owner only", written through a SECURITY DEFINER function; a prune job for the 2 h expiry |
| One run per thread | `asyncio.Lock` per thread id in a process-local dict (`corpai/agents/_runner.py`, `_thread_locks`) | a Postgres advisory lock on the thread id: try-lock for a new run (busy → 400 as today), blocking lock for a resume |
| Model concurrency | `asyncio.Semaphore` per route per event loop (`corpai/gateway/gateway.py`) | divide `max_concurrency` by the worker count, or a shared limiter; the inference server's own queue is the real limit |
| Audit write concurrency | 16 per event loop, a new connection per write (`corpai/audit/log.py`) | a small dedicated pool per worker; count connections (below) |
| Checkpoint connections | pool of 16 per process, one connection held per running graph (`corpai/agents/_checkpoint.py`) | fine per worker; size against `max_connections` |
| Signing keys | files under `$DATA/secrets`, created on first use (`corpai/identity/keys.py`) | the same keys on every worker, from the secret store (section 8) |
| Interrupted proposals | `fail_interrupted()` at every API start marks proposals `approved` for more than 300 s as failed (`corpai/proposals/service.py`, called from `corpai/api/app.py`) | run it from one scheduled job, or give executions a lease, so a restarting worker cannot fail another worker's execution |
| Metric refresh | the CFO's demo button `POST /api/metrics/refresh` (`corpai/api/routes_metrics.py`) | a scheduled job after the month-end close; the refresh already takes an advisory lock (`corpai/metrics/refresh.py`) |
| Checkpoint TTL | `python -m corpai prune` by hand (`corpai/cli/prune.py`) | a nightly scheduled job |

Already safe across workers: outage switches and budgets (in `ops` tables), the person-name gazetteer (each worker loads it at start-up and the runner re-checks `ops.name_source_fingerprint()` before every run, `corpai/agents/_gazetteer.py`), LangGraph checkpoints (in the `lg` schema), proposals and the audit chain (the insert trigger serialises appends with an advisory lock).

The MCP servers can stay in-process (`McpClient(asgi_app=app)` in `corpai/api/app.py`). If they are split out, set `CORPAI_API_BASE` and change tool tokens from HS256 to an asymmetric algorithm (`corpai/identity/tokens.py`): with a shared HMAC key, every server that can verify a tool token can also mint one for any user.

## 7. Database

**Today** pgserver runs Postgres 16 with pgvector embedded, on loopback, with password-less roles: `corpai/db/pg.py` builds the DSNs from pgserver's URI (`_swap`). The API process also connects as `corpai_owner` for several jobs: migrations at start-up, proposal executors, the checkpoint schema setup, the gazetteer loader and the audit anchors and chain check ([architecture §4](architecture.md#4-database)).

**Change.**

- A real Postgres 16 with pgvector (a VM pair with streaming replication, or an EU-hosted managed service that offers pgvector and allows RLS and SECURITY DEFINER functions). DSNs and credentials come from configuration; `corpai/db/pg.py` is frozen core, so this is a spec change first.
- Roles with SCRAM passwords or client certificates; `pg_hba.conf` admits `corpai_app` only from API hosts and `corpai_owner` only from the job runner.
- **Take the owner role away from the internet-facing API.** Move proposal execution, migrations, `setup_schema`, anchors and chain verification to a job runner (a worker process with its own queue). The API then holds only `corpai_app` credentials, so a compromised API process still cannot bypass RLS. Executors already run from the approved row and re-check the params hash, so they can move without changing their logic.
- Connection budget per API worker today: app pool 10 (`corpai/api/app.py`) + checkpoints 16 + audit up to 16, so three workers need well over Postgres' default `max_connections` of 100. PgBouncer in transaction mode works for `principal_tx` (it sets the principal with transaction-local `set_config(..., true)`) and for audit writes (one function call per connection), given a PgBouncer version that handles psycopg's prepared statements; it does **not** work for the checkpoint store, which sets the principal's variables at session level on a connection it holds for the whole run (`corpai/agents/_checkpoint.py`); give that pool direct connections or a session-mode pool.
- Migrations stay plain SQL and keep their sha256 check (`corpai/db/migrate.py`); apply them from the deployment pipeline, not from every worker's start-up.

## 8. Secrets

| Secret | Today | Production |
|---|---|---|
| Model API keys | environment variable named by `api_key_env` in `config/models.yaml`, read at call time (`corpai/gateway/backend.py`) | injected from the secret store at process start; a separate key for the local server (`vllm --api-key`) so `local` and `external` never share one |
| Session, tool and gateway signing keys | three files in `$DATA/secrets`, created on first use; deleting a file logs everybody out (`corpai/identity/keys.py`) | the secret store, with a key id in the token header so keys can rotate with overlap; asymmetric keys for tool tokens if MCP servers are split out |
| Audit anchor key | `$DATA/secrets/audit_anchor.key` (`corpai/audit/anchor.py`) | a KMS or HSM key that the database host cannot read; better, an asymmetric signature so verification needs no secret |
| Database credentials | none (pgserver, loopback) | per role, in the secret store, rotated |
| OIDC client secret | — | the secret store |

Use one secret store (HashiCorp Vault, or the KMS and secret manager of the EU hosting provider). Nothing secret goes into `config/`, the repository, logs, traces or audit details; the existing code enforces the last two (`corpai/audit/log.py` refuses credential-like keys and values, traces refuse credential attribute keys; tokens never enter graph state, [architecture §5](architecture.md#5-identity-groups-and-tokens-a3)). Logs are kept clean by convention only: there is no logging filter, so add one that runs `scrub_secrets` on every record.

## 9. Audit anchors on storage the database host cannot rewrite

**Today** `audit.log` is append-only and hash-chained inside Postgres (triggers refuse update, delete and truncate). The chain alone cannot catch a cut tail or a history re-hashed by someone who controls the table, so every owner job appends the chain head to `$DATA/audit_anchors.jsonl`, HMAC-signed with `$DATA/secrets/audit_anchor.key`, and `verify_chain()` checks every anchored row (`corpai/audit/anchor.py`). Both files sit on the same machine as the database, and rows after the newest anchor can still be dropped unnoticed ([architecture §9](architecture.md#9-audit-and-trace-a1)).

**Change** in `corpai/audit/anchor.py` (`write_anchor`, `read_anchors`):

- Write each anchor as an object to S3-compatible storage with **Object Lock in compliance mode** (WORM), with credentials that can only put objects: neither the database host nor a platform admin can delete or overwrite an anchor before its retention ends. Keep the local file as a cache.
- Sign anchors with a key the database host cannot read (section 8).
- Anchor on a schedule (for example every 5 minutes, from the job runner), not only when an owner job runs, so the unanchored tail is short.
- Run `verify_chain` daily and alert on `first_bad_seq`. The security auditor's `GET /api/audit` already reports the chain status.

## 10. Observability

**Today** traces are rows in `trace.spans`, written through `trace.write_span()`; text is masked once, at span end, by `default_masker` (secret scrubbing, then `mask_for_level`; L4 spans keep no text) in `corpai/trace/span.py`. The requester reads their own spans, the platform admin metadata only, heads per-department counts with a minimum group of 5. There is no metrics or alerting pipeline.

**Change.**

- Export spans to **OpenTelemetry** (an OTel collector feeding Tempo/Jaeger and Prometheus/Grafana) or to a **self-hosted Langfuse** in the EU, from `Tracer` in `corpai/trace/span.py`, **after** masking: the exporter receives exactly the text that would be stored in `trace.spans`, and L4 spans export metadata only. Keep the in-database spans for the user-facing trace viewer, or replace them once access rules are rebuilt in the new tool.
- **Do not enable library auto-instrumentation** that captures prompts before the gateway masks them (OpenAI SDK instrumentation, LangChain callbacks). In particular `langsmith` is installed as a LangGraph dependency, and setting `LANGSMITH_TRACING=true` makes LangChain attach a tracer that sends graph state to the LangSmith service, past `config/egress.yaml` and the masking. Importing `corpai` forces the tracing variables to `false` (`corpai/__init__.py`, issues-log FN-25); keep that, and block the host at the egress proxy as well (see also [compliance/egress-inventory.md](../compliance/egress-inventory.md)).
- Metrics and SLOs, derived from what the gateway already measures (route, latency, attempts and token usage are recorded in the `model_call` audit rows, `corpai/gateway/gateway.py`):

| SLI | Target (example) |
|---|---|
| API availability, business hours | 99.5 % |
| `qa` answer latency, p95 | < 10 s on `local` |
| `gateway_busy` and `local_unavailable` errors | < 1 % of model calls |
| Legal review of a 20-clause contract, p95 | < 5 min |
| Audit chain verification | daily, always ok |

- Alerts: local route down (the `local_down` switch and failing health checks of the inference server), budget exhaustion per department, egress proxy denials, `verify_chain` failure.

## 11. Network and egress

**Today** egress is enforced in process: `config/egress.yaml` lists every destination with its hosts and maximum level, `check_egress()` runs before anything is sent, the live HTTP client re-checks the host of every request including redirects and ignores proxy variables (`corpai/gateway/backend.py`, `corpai/gateway/egress.py`), external calls are logged in `ops.egress_log` before sending, and a test fails on any host literal in `corpai/` or `config/` that is not listed. The `web` MCP server is a mock corpus (`search.mock`, `in_process`).

**Change.** Add the network layer underneath:

- API hosts have no direct route to the internet. Outbound HTTPS goes through one forward proxy (Squid or Envoy) whose allowlist is **generated from `config/egress.yaml`** (`http` destinations and their hosts), so the inventory stays the single source.
- Because the backend ignores proxy environment variables on purpose, the proxy has to be configured explicitly per destination (a new field in `config/egress.yaml`, used in `corpai/gateway/backend.py`).
- Reconcile daily: every external request in the proxy log must have a row in `ops.egress_log`, and vice versa.
- Zones: reverse proxy → API workers → database and GPU host; the database and GPU host accept connections only from the API workers and the job runner.
- A real web search for `sales_profile` becomes a new `http` destination with `max_level: L1` and its own domain allowlist (`corpai/mcp_servers/web_allowlist.py`, `corpai/mcp_servers/web.py`).
- Python packages come from an internal mirror at build time; nothing installs at runtime. The same goes for the embedding model: with `CORPAI_EMBED=fastembed`, fastembed downloads it from huggingface.co on first use (`embed.model_download` in `config/egress.yaml`, no company data). Copy `.cache/fastembed` into the image at build time and set `HF_HUB_OFFLINE=1`, so the proxy never needs to allow that host.

## 12. Backups and retention

**Backups.** Continuous WAL archiving with point-in-time recovery (pgBackRest or Barman), encrypted, stored in the EU, with a restore test every quarter. Targets for a company this size: RPO 15 minutes, RTO 4 hours. The anchor bucket keeps its own retention and is not part of the database backup. Losing the session or tool keys only logs everyone out; losing the anchor key makes old anchors unverifiable, so it is backed up by the KMS.

**Retention**, to be set with legal counsel and written into the record of processing:

| Data | Today | Proposal |
|---|---|---|
| `audit.log` | kept forever; triggers refuse delete for everyone | keep for a fixed period (for example 2–5 years); remove whole yearly partitions with a documented, audited superuser procedure and a final anchor |
| `trace.spans` (masked text up to L3) | kept forever; an erasure request reaches them through `trace.erase_subject()` (migration 103, issues-log D-04) | 30–90 days |
| LangGraph checkpoints (`lg`), which hold full graph state including L3/L4 content | `prune` after `CORPAI_CHECKPOINT_TTL_DAYS` (30) idle days, by hand | the same TTL as a nightly job; shorter for HR threads |
| Uploaded contracts | 2 h in memory | 2 h in the new table |
| `hr.screening_evidence`, `hr.screenings`, candidates | kept | a fixed period after the requisition closes, then deleted; candidates are told in the privacy notice (GDPR Art. 13) |
| `ops.egress_log` (a copy of every payload sent to the external route, L1–L2) | kept | long enough for reviews and incident response, for example 12 months |
| `legal.reviews`, proposals, outbox | kept | as business records of the legal and sales processes |
| `metrics.snapshots` | kept | derived from the ledger; the ledger itself follows the Swedish Bookkeeping Act (seven years) in the finance system |

Erasure and backups: an erasure request removes the data from the live database; keep an erasure register and re-apply it after any restore.

## 13. Cost model

Rough monthly figures for 2026; replace them with quotes.

| Item | Estimate | Basis |
|---|---|---|
| GPU server, 2 × 48 GB data-centre GPUs | €1,000–1,500 per month | €30–50k purchase written off over 3 years, plus power (~1 kW) and rack space |
| *or* rented EU GPUs | €500–3,500 per month | €1–2.5 per GPU-hour; business hours only vs 24/7, one or two GPUs |
| External model API (if kept) | tens to low hundreds of € per month | e.g. 40 users × 20 calls × 3,000 tokens ≈ 2.4M tokens per working day, ~50M per month, of which only the L1–L2 share goes external |
| Postgres pair, job runner, observability, object storage | €200–500 per month | small VMs and storage at an EU provider |
| IdP | €0 extra (Entra ID with existing Microsoft 365) or a VM (Keycloak) | |
| People | the largest item: ~0.5 FTE platform engineering, plus security and compliance work (DPIA, AI Act, audits) | |

The daily budgets cap the worst case: `ops.budget_defaults` allows 2,000,000 tokens per department per UTC day (`'*'` fallback, migration 010), so seven departments can never exceed 14M tokens a day, whatever an agent does. Measure real usage from `ops.budgets.tokens_used` and the `usage` in `model_call` audit rows before choosing hardware or a contract.

## 14. Rollout plan

Each phase has an exit criterion; any app can be switched off by removing it from `start` in `config/agents.yaml`, and the outage switches stop either route at once.

| Phase | Scope | Exit criterion |
|---|---|---|
| 0. Foundations (4–6 weeks) | Postgres, IdP login, secret store, egress proxy, backups, observability, anchor bucket; GPU host and model choice by running the eval suites live against it; DPIA and record of processing; consultation with the employee representatives under the Swedish Co-determination Act (MBL) before introducing the system; AI literacy training for staff (EU AI Act Art. 4) | evals pass on the local model; restore test done; penetration test of the login and API |
| 1. Pilot | `qa` for IT and one department | a month without a routing incident: a weekly sample of the external payloads in `ops.egress_log` reviewed by a person and re-classified, nothing confidential found, no canary leaked; latency within the SLO |
| 2. Company-wide Q&A and figures | `qa` for everyone; metric sign-off for the CFO with a scheduled refresh; management reports for the CEO and heads | the CFO signs a real month-end from the platform; report numbers match the finance system |
| 3. Legal and sales | `legal_review` (after the live legal evals meet the harness thresholds), `sales_profile` with a real web search behind the egress proxy | lawyers accept the review quality on real contracts; injection tests pass on the new web source |
| 4. HR, last | `hr_headcount`, then `hr_screening` only after a separate assessment: employment use is high-risk under the EU AI Act (Annex III), candidates are told how the tool is used, bias tests on anonymised real data, and the decision whether to use it at all | sign-off by HR, legal and the employee representatives |

## 15. Checklist of code and config changes

| File | Change |
|---|---|
| `config/models.yaml` | `local`: real `base_url`, `model`, own `api_key_env`, `simulated_onprem: false`, realistic `max_concurrency` / timeouts; `extra_body` per route |
| `config/egress.yaml` | `model.local` → the internal inference host; `model.external` → the EU provider (or the local host); proxy per destination; a real web search destination |
| `corpai/gateway/conf.py`, `backend.py` | per-route `extra_body`; an explicit egress proxy; the company CA only if it is not in the system trust store |
| `corpai/api/routes_core.py`, `security.py`, `ui/js/view_login.js`, `ui/index.html` | OIDC login; `Secure` cookie; CSP without `http://127.0.0.1:8000` |
| new directory sync job (`corpai/cli/`) | users, memberships and grants from HR system / IdP / business systems |
| `corpai/agents/legal/uploads.py` + migration | uploads in a table |
| `corpai/agents/_runner.py` | advisory lock per thread |
| `corpai/proposals/service.py`, `corpai/api/app.py` | `fail_interrupted` as a scheduled job or with leases; executors in the job runner |
| `corpai/identity/keys.py`, `tokens.py` | keys from the secret store, key ids, asymmetric tool tokens if MCP is split out |
| `corpai/db/pg.py` (frozen core, spec change first) | DSNs and credentials from configuration |
| `corpai/audit/anchor.py` | WORM sink, KMS-held key, scheduled anchors |
| `corpai/trace/span.py` | OpenTelemetry / Langfuse exporter after masking; tracing auto-instrumentation pinned off |
| `corpai/api/routes_metrics.py`, `corpai/cli/prune.py` | refresh and prune as scheduled jobs |
| `corpai/mcp_servers/web.py`, `web_allowlist.py` | a real search backend behind the egress proxy |
