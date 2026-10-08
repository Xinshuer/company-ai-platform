> **Fictional company, synthetic data. Illustrative, not legal advice.**

# Egress inventory

Every place data can leave the platform process, the highest data level allowed there, what is sent, on what legal footing, and the code that enforces it. Version 0.1, 2026-10-08. Status markers: [README.md](README.md).

The authoritative list is [config/egress.yaml](../config/egress.yaml). This document adds what the code can reach without being in that list.

## How egress is controlled

- **One exit for model traffic.** Agents hold no API keys. Every model call goes through `ModelGateway.chat` ([corpai/gateway/gateway.py](../corpai/gateway/gateway.py)), and only `corpai/gateway` imports a model SDK (`tests/gateway/test_egress.py::test_only_the_gateway_imports_model_sdks`).
- **Route by level, fail closed.** `level = max(hwm, classify_text(...))`; `external` only if the level is at most L2; an outage of `local` is an error, never a reason to go `external`; a DLP scan runs before every external call and moves blocked calls to `local`.
- **Inventory check before sending.** `check_egress(destination, url, level)` ([corpai/gateway/egress.py](../corpai/gateway/egress.py)) refuses an unlisted destination, a host not listed for it, a non-https URL or a level above `max_level`. It also runs in fake mode. The live HTTP client re-checks the host of every request, redirects included, follows no redirects, and ignores proxy variables (`trust_env=False`; [corpai/gateway/backend.py](../corpai/gateway/backend.py)).
- **Static check.** A test scans `corpai/` and `config/` for host literals and fails on any host not in the inventory (`tests/gateway/test_egress.py::test_no_unlisted_hosts_in_code_or_config`).
- **Logged before sent.** For `external` calls, the full payload is written to `ops.egress_log` in the same transaction as the budget reservation, before anything is sent. If the row cannot be written, nothing is sent. The table and `ops.log_egress()` refuse external rows above L2 (`migrations/010_a4_ops_gateway.sql`).

## Summary

| Destination | Kind | Hosts | Max level | Data sent | Personal data | Legal basis | Third country / mechanism | Enforced by | Status |
|---|---|---|---|---|---|---|---|---|---|
| `model.external` | http | `api.deepseek.com` | L2 | Prompt messages, tool definitions, model parameters | Not by design (personal data is L3); possible if the classifier misses a name | That of the activity ([ropa.md](ropa.md)); none needed for non-personal L1/L2 content | **China**, no adequacy decision. SCCs + transfer impact assessment needed; none in place **[counsel]** | Gateway rules 3–5, `check_egress`, `check_host`, `ops.egress_log` CHECK | [implemented] |
| `model.local` | http (`simulated_onprem`) | `api.deepseek.com` | L4 | Same, at any level: contract text, CRM data, redacted résumés, report data | **Yes**, including possible special categories | That of the activity ([ropa.md](ropa.md)) | **China, today** (meant to be on-prem, then no transfer). No mechanism in place | `check_egress`, `check_host`; **not** in `ops.egress_log` | [implemented] as a simulation; not acceptable for real data |
| `search.mock` | in_process | none | L1 | Nothing leaves: queries are answered from `corpus_dir/web/pages.jsonl` | No | n/a | n/a | `web.search`/`web.fetch` checks, allowlist | [implemented] |
| `email.outbox` | simulated | none | none (no model payloads) | Nothing leaves: approved e-mails and contracts become rows in `ops.outbox` | Recipient address, name, body | 6(1)(f) (sales follow-up, [LIA](lia-sales-profiling.md)); 6(1)(b)/(f) (contracts) | n/a today; a real e-mail provider needs an Art. 28 contract, EU hosting preferred | Proposals with four-eyes approval; recipient CHECK `*.example` | [implemented] |
| `embed.model_download` | http (opt-in) | `huggingface.co` (the Hub may redirect to storage hosts under `hf.co`) | none (no payloads) | An HTTP download request for the embedding model (IP address, user agent) | Operator's IP address only | 6(1)(f), if the IP counts as personal data | USA; no platform data, so no transfer mechanism beyond this record **[counsel]** | Listed in the inventory, but fastembed's own downloader does not go through `check_egress` (issues-log D-17) | [partial] |
| LangSmith (LangChain tracing) | not listed, **forced off** | `api.smith.langchain.com` (SDK default) | n/a | Full graph inputs, outputs and state, if it were switched on | **Yes**, up to L4 | None: must not be used | USA; none | Importing `corpai` sets `LANGSMITH_TRACING[_V2]` and `LANGCHAIN_TRACING[_V2]` to `false` ([`corpai/__init__.py`](../corpai/__init__.py)); `test_langsmith_tracing_cannot_be_switched_on_by_environment` | [implemented] |

## Listed destinations

### `model.external`: DeepSeek API, public and internal data

- **Used by.** Q&A answers on public or internal questions; the HR headcount note at L2; sales profile extraction when every prompt field is published text (`public_only`, session L1); any other model call whose final level is at most L2.
- **Data sent.** OpenAI-format `messages`, `tools`, `max_tokens`, `temperature`, `response_format`, `extra_body` (thinking disabled). Secrets are scrubbed first (rule 1). No user identifier: `BackendCall` carries no principal, and the request sets no `user` field.
- **Legal basis.** For L1/L2 content that holds no personal data, none is needed. Where incidental personal data slips through, Art. 6(1)(f) for the activity it belongs to ([ropa.md](ropa.md)).
- **Processor.** Yes. An Art. 28 contract is needed. **None exists**: the simulation uses a developer API key and synthetic data.
- **Transfer.** DeepSeek processes personal data in China (its privacy policy; its API terms name no storage location and say nothing on retention or training use: design-review §9, open). There is no adequacy decision for China. An Art. 46 safeguard (standard contractual clauses) plus a transfer impact assessment would be needed, and whether they can work for this recipient is doubtful. **[counsel]**
- **Kept here.** `ops.egress_log` holds every external payload with user id, run id, level and SHA-256 (owner-only table). No retention limit yet ([retention-matrix.md](retention-matrix.md)).

### `model.local`: simulated on-prem model, today DeepSeek

- **Used by.** Everything at L3/L4: Q&A with personal or confidential content, legal review and drafting, the report writer, the sales strategy and follow-up e-mail, **HR screening** (the evidence table refuses any route but `local`).
- **Data sent.** As above, with confidential and restricted content.
- **Real-world implication (decision D3).** The design treats `local` as inside the company, so it is not written to `ops.egress_log` and allows L4. Today the host is the same third-country API as `external`. Using real data would be a transfer of personal data, possibly including special categories, to China, with no processor contract and no transfer mechanism. **Not acceptable for real data** ([README.md](README.md)).
- **What a real deployment changes [recommendation].**
  - Point `local` at a model ABC Tech runs: `base_url`/`model` in [config/models.yaml](../config/models.yaml), the host in `config/egress.yaml` (for example an internal vLLM host, https only), and `simulated_onprem` removed.
  - Until then, refuse `CORPAI_LLM=live` while `simulated_onprem` is true and the database holds non-synthetic data; or log `local` egress like `external` for as long as it leaves the machine.

### `search.mock`: in-process mock web

- **Used by.** `web.search` and `web.fetch` ([corpai/mcp_servers/web.py](../corpai/mcp_servers/web.py)), only through the `sales_web` sub-agent.
- **What could leave.** Nothing: no network. The checks are written as if it were real. The query must look like a company name (no `@`, no URL, at most 8 words and 4 digits) and is classified with `public=False` before `check_egress("search.mock", None, level)` with `max_level: L1`. Fetch accepts only `https` URLs on hosts that are page hosts of the synthetic corpus and end in `.example`, with no query string or fragment ([corpai/mcp_servers/web_allowlist.py](../corpai/mcp_servers/web_allowlist.py)). A URL that a page merely mentions (an attacker's address) is not a page host, so it is not allowed.
- **Real deployment [recommendation].** A real search API and HTTP fetch become a new `http` destination with `max_level: L1`. The search provider receives company names (not personal data); fetched sites receive the fetch request. Keep the allowlist to company sites, trade press and public registers; never social networks ([lia-sales-profiling.md](lia-sales-profiling.md) S9).

### `email.outbox`: simulated e-mail and contract sending

- **Used by.** Executors of approved `crm.send_email` and `legal.send_contract` proposals ([corpai/proposals/executors.py](../corpai/proposals/executors.py)).
- **What could leave.** Nothing: a row in `ops.outbox`, whose `recipient` CHECK allows only `*.example` addresses (`migrations/061_b2_outbox.sql`). `max_level: null` means no model payload may ever be sent there.
- **Real deployment [recommendation].** An e-mail provider (SMTP or API) becomes a processor: an Art. 28 contract, EU hosting preferred. It receives the recipient's address and name and the message. Add the opt-out footer and suppression check first ([lia-sales-profiling.md](lia-sales-profiling.md) S7–S8).

### `embed.model_download`: Hugging Face Hub, embedding model download (issues-log D-17)

- **When.** Only with `CORPAI_EMBED=fastembed`, and only when the embedding model (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, which fastembed fetches as a quantised ONNX build) is not yet in `.cache/fastembed` ([corpai/embed/fast.py](../corpai/embed/fast.py)). Tests and the default configuration use the fake embedder and never download.
- **Data sent.** Model files come in; no platform content goes out. Queries and documents are embedded locally. The download request reveals the machine's IP address and the model name to Hugging Face (USA).
- **Legal basis.** Art. 6(1)(f) for the operator's connection metadata, if it counts as personal data at all.
- **Enforcement.** `max_level: null`, so `check_egress` refuses any payload to this destination (`tests/gateway/test_egress.py`). The download itself runs in fastembed's and `huggingface_hub`'s own HTTP clients, which do not call `check_egress`. The entry documents the path; it does not guard it.
- **Recommendation.** Copy `.cache/fastembed` from a build machine as a deployment step, then run with `HF_HUB_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1`, so the entry is never used (this is also the note in `config/egress.yaml`).

## Reachable but not in the inventory

### LangSmith tracing (LangChain / LangGraph)

- **When.** `langsmith` is installed as a dependency of `langchain-core` ([requirements.lock.txt](../requirements.lock.txt)). LangGraph runs would be traced to LangSmith whenever the environment set `LANGSMITH_TRACING=true` or `LANGCHAIN_TRACING_V2=true` and an API key.
- **Data sent if switched on.** Node inputs, outputs and graph state, which hold retrieved passages, CRM rows, contract text and HR evidence up to L4. That would bypass both the masking in `corpai/trace` and the routing rules: exactly the "hosted trace as an unlisted exit" that design-review gap 2 warns about.
- **Control (final review, issues-log FN-25).** Whatever imports `corpai` (serve, demo-record, evals, tests) sets `LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING` and `LANGCHAIN_TRACING` to `false` before LangChain reads them, and says so on stderr when one was on ([`corpai/__init__.py`](../corpai/__init__.py)). `tests/gateway/test_egress.py::test_langsmith_tracing_cannot_be_switched_on_by_environment` sets both variables and checks that a graph run gets no LangSmith tracer. Tracing switched on in code (`langsmith.tracing_context(enabled=True)`) is not blocked; code review must catch it.

### Other paths checked

| Path | Leaves the machine? | Note |
|---|---|---|
| MCP servers (`/mcp/<domain>/mcp`) | No | Mounted in the API process; `McpClient(asgi_app=app)` calls them in-process. `CORPAI_API_BASE` defaults to `http://127.0.0.1:8000`. |
| OpenAI-compatible endpoint `POST /v1/chat/completions` | Inbound, not egress | Calls go through `ModelGateway.chat` with the same rules. No platform code mints gateway tokens today. |
| Vendored Clause Check | No | Runs in `gateway` mode. Its optional `local` mode calls the platform's own `http://127.0.0.1:8000/v1`. Its Langfuse tracing hooks are no-ops ([corpai/legal/clausecheck/tracing.py](../corpai/legal/clausecheck/tracing.py)). |
| `corpai/trace` | No | Spans live in `trace.spans`. Langfuse export (decision E6) is not implemented. |
| Browser UI | No third-party requests | CSP `default-src 'self'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:8000`. No CDNs or web fonts. External links show the full URL and ask before opening (a user's own navigation, not platform egress). |
| OpenTelemetry | No | Only `opentelemetry-api` is installed; there is no SDK or exporter. |
| `pip install` (PyPI) | Build time only | Not runtime egress. Supply chain: pinned versions in `requirements.lock.txt`. |
| Model provider SDK telemetry | No | The `openai` client is built per call with `max_retries=0` against the listed host only. |

## Questions for counsel

1. Can any Chapter V mechanism support personal data going to DeepSeek? If not, confirm the rule that only L1/L2 (non-personal) data may use it.
2. An Art. 28 contract with whichever provider serves `local` and `external` in production.
3. Whether the operator's IP address in the model download (Hugging Face, USA) needs any documentation beyond this inventory.
