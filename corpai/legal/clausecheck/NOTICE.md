# Clause Check (vendored)

This package is copied from the author's own repository **Xinshuer/Contract-Clause-Reviewer**, commit
`040e039` ("Harden the review loop, the eval harness and the API"), package `clausecheck/`, by the same
author as this platform.

## Copied

| File | Status |
|---|---|
| `models.py` | unchanged except one added topic, `service_levels` (Kestrelbyte's playbook has a service-credit rule) |
| `parse.py`, `playbook.py`, `tools.py`, `redline.py`, `pipeline.py` | unchanged |
| `split.py` | the clause-heading regex also accepts a leading Markdown heading marker (`## 5. Liability`), the format of the platform's contract store and corpus |
| `review.py` | `AnthropicBackend` removed; `review_clause()` raises in mode `gateway` (that path needs a principal, a run and an hwm, so the caller builds `corpai.legal.backend.GatewayBackend` and calls `model_review` itself). The prompts, `OpenAICompatBackend`, the bounded loop `model_review`, `_finalise_redline` and the rule-based mock are unchanged |
| `config.py` | rewritten as a plain dataclass: modes `gateway`, `local` (any OpenAI-compatible endpoint, here the gateway's `/v1/chat/completions`), `mock`; nothing is read from the environment or a `.env` file, no provider key fields, no Anthropic/DeepSeek modes, no auto-detection probe; `playbook_path` has no default (the original defaulted to its own `data/playbook.json`, which is not vendored), so `pipeline.review_contract` needs an explicit playbook or path and never silently runs with Kestrelbyte's rules |
| `tracing.py` | the no-op branch only (Langfuse removed) |
| `__init__.py` | exports `contract_from_text` as well |

## Not copied

- `graph.py` (LangGraph with a SQLite checkpointer): the platform has its own LangGraph runtime with a
  Postgres checkpointer and approval gates.
- `api.py`, `cli.py`, `app/streamlit_app.py`: the platform has its own API and UI.
- `data/playbook.json`, `data/sample_contract.txt`: the customer-side playbook is replaced by Kestrelbyte's
  provider-side playbook in `corpai/legal/data/playbook.json` (same rule format).
- `evals/`: the golden set was rebuilt for Kestrelbyte in `evals/legal/` from the same case structure.

## Why

- **No direct provider access.** In the platform every model call goes through the model gateway (routing by
  data level, DLP, budget, audit). Anthropic's SDK, provider keys in settings and `.env` loading would be a
  second, unaudited exit.
- **No hosted tracing.** Langfuse would receive contract text; the gateway already traces every call, masked by
  level.
- **The review loop is kept as is**, so the platform's `GatewayBackend` and the unchanged `OpenAICompatBackend`
  plug into the same `step / append_tool_results / request_json` interface and the same bounded loop.
