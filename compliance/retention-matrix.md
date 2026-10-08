> **Fictional company, synthetic data. Illustrative, not legal advice.**

# Retention matrix and data subject rights

Each data store the platform writes: what personal data it holds, how long it is kept, which deletion is **actually implemented** (with the code), what is not implemented yet, and how access, erasure and objection requests reach it. Version 0.1, 2026-10-08. Status markers: [README.md](README.md).

Short version: **two stores have a working time-based deletion.** LangGraph checkpoints are deleted by `python -m corpai prune`, a command nothing schedules yet. Legal uploads expire from memory after 2 hours. Traces have an erasure function for one data subject, `trace.erase_subject`, but no time limit. Every other store keeps its data until someone deletes it by hand. The audit log cannot be deleted from at all, by design.

## 1. Matrix

"Recommended" values are proposals for a real deployment. The legal periods marked [counsel] need confirmation.

| # | Store | Personal data | Level | Kept today | Deletion implemented | Recommended | How to find a person |
|---|---|---|---|---|---|---|---|
| 1 | LangGraph checkpoints `lg.checkpoints`, `lg.checkpoint_blobs`, `lg.checkpoint_writes`, and their thread rows `ops.threads`, `ops.thread_runs` | Whole graph state of a conversation: question, retrieved passages, tool results, model answers, HR evidence, sales contacts. Up to L4 | ≤ L4 | Until the thread has been idle for 30 days **and** someone runs prune | **[implemented]** `python -m corpai prune [--days N]` ([corpai/cli/prune.py](../corpai/cli/prune.py) → `prune_sync` in [corpai/agents/_checkpoint.py](../corpai/agents/_checkpoint.py)). Deletes threads whose newest checkpoint is older than N days (default `checkpoint_ttl_days` = 30, env `CORPAI_CHECKPOINT_TTL_DAYS`), and threads that never got a checkpoint and were opened that long ago. Removes checkpoints, blobs and writes, then the `ops.threads` row; `ops.thread_runs` follows by `ON DELETE CASCADE` (migration 100). Audited as `owner_job` `prune` with counts. | Keep 30 days. **[recommendation]** Run prune daily from a scheduler; nothing calls it today. | By owner: `ops.threads.owner_id`. By third party: no index (state is opaque). For one HR candidate: the `hr_screening` audit row's `run_id` → `ops.thread_runs` → `thread_id`. |
| 2 | Legal contract uploads (process memory) | Full contract text, possibly with counterparty names | untrusted, classified on review | 2 hours; at most 10 per user (the oldest is dropped); lost on restart | **[implemented]** `TTL_SECONDS = 2 * 3600`, `MAX_PER_USER = 10`, `_prune()` on every put and get ([corpai/agents/legal/uploads.py](../corpai/agents/legal/uploads.py)) | As implemented. In a multi-worker deployment, a database table with the same owner rule and TTL (architecture §17) | Uploader only |
| 3 | Web page text in the `sales_web` sub-graph | Names on public pages, if any | L1 | Only for the duration of the run | **[implemented]** The sub-graph is compiled with `checkpointer=False`; only the validated profile returns to the parent ([corpai/agents/sales/web.py](../corpai/agents/sales/web.py)) | As implemented | — |
| 4 | RAG `rag.documents`, `rag.chunks` (text, embeddings) and the corpus files under `<data dir>/corpus` | Names, roles and contact details inside documents; `subject_ids` from the document's front matter | L1–L4 | Indefinite. A changed document gets a new version and the old one is marked `superseded`; a document gone from the corpus is marked `superseded` too. **Nothing is deleted.** | Supersede only ([corpai/rag/ingest.py](../corpai/rag/ingest.py)). `synth` does not touch `rag.*`. | **[recommendation]** Delete superseded versions after 90 days unless a legal hold applies; an owner job that deletes documents and chunks by `subject_ids` for erasure. | `rag.documents.subject_ids`, `rag.chunks.subject_ids` (label columns); free text otherwise |
| 5 | Audit log `audit.log` | Actor user id, agent chain, resource ids (`hr.candidate:…`, `crm.contact:…`), decision, reason code, route, level, run id, metadata details. No message text, no credentials | metadata | Indefinite | **None, by design.** Triggers reject UPDATE, DELETE and TRUNCATE for everyone (migration 001). `python -m corpai db reset --yes` deletes the whole development database. | **[recommendation, counsel]** 24 months, and at least 6 months for the HR screening system (AI Act Art. 26(6)). Implement as monthly partitions dropped whole after the period, with an anchor recorded at each cut so the chain stays verifiable. Today's append-only triggers would have to allow exactly that. | `actor`, `resource`, `run_id` |
| 6 | Audit anchors `<data dir>/audit_anchors.jsonl`, and archived `audit_anchors.before-reset-<UTC>.jsonl` after a reset | Sequence numbers, hashes, timestamps; no personal data | — | Indefinite | `db reset --yes` renames the anchor file instead of deleting it (`archive_anchors` in [corpai/cli/db.py](../corpai/cli/db.py); issues-log WI-04) | Delete together with the log segment they cover | — |
| 7 | Traces `trace.spans` | User id and department, span metadata, `subject_ids` of what tool and retrieval spans read; input and output text **masked by level**: L1/L2 as written, L3 with every sensitive span replaced by `⟨kind⟩`, L4 no text (CHECK `spans_l4_metadata_only`). Model-call spans keep the masked prompt, for HR screening the redacted résumé | ≤ L3 text | Indefinite | **[partial]** Erasure only: `trace.erase_subject(id)` (owner only) blanks input and output text of every span of every run in the threads where some span read that subject, and removes the id (migration 103, issues-log D-04; `tests/trace/test_trace_subjects.py`). No time-based deletion. | **[recommendation]** Drop `input_text`/`output_text` after 30 days and delete spans after 12 months, as an owner job like prune. Record subjects on the spans of domain-code model calls too (HR screening first). | `user_id` (the requester); `subject_ids` (GIN index) for tool and retrieval reads. **Not** for model calls made by domain code (HR screening, legal review of pasted text, report, sales extraction). |
| 8 | Proposals `ops.proposals` | Requester, approver, full parameters (e-mail recipient, subject and body; contract drafts), summary, decision reason | ≤ L4 | Indefinite. `expires_at` (created + 7 days) only ends the approval window; the row stays. | **None** | **[recommendation]** Pending, expired, rejected and failed proposals: delete after 90 days. Executed ones: keep as long as the business record they produced (row 9, CRM). | `subject_ids` (contact ids for `crm.*` and `legal.send_contract`), `requester_id`, `approver_id` |
| 9 | Simulated outbox `ops.outbox` | Recipient address, subject, body of sent e-mails and contracts | ≤ L4 | Indefinite | **None** | **[recommendation]** As long as the CRM contact (24 months after the last interaction unless a customer relationship exists); contracts per contract retention. | `subject_ids` (contact id), `recipient` |
| 10 | HR screening evidence `hr.screening_evidence` | Per requirement: verdict, verbatim quote from the redacted résumé, flags; candidate id; created by | L4 | Indefinite (`synth` empties it through `TRUNCATE … CASCADE`, development only) | **None** | **[recommendation, counsel]** End of the recruitment plus 2 years (to answer a discrimination claim); for the hired candidate, only what the personnel file needs. See [dpia-hr-screening.md](dpia-hr-screening.md) M11. | `subject_ids` = `{candidate_id}` (set by `hr.write_screening_evidence`) |
| 11 | HR decisions `hr.screenings` | Decision, reason (free text by HR), decided by and at, copy of the evidence items seen | L4 | Indefinite (`synth` truncates it, development only) | **None** | As row 10 | `subject_ids` = `{candidate_id}` |
| 12 | Candidates `hr.candidates` (system of record, here synthetic) | Name, e-mail, phone, full résumé (may contain special categories), source, stage | L3 | Indefinite | **None** (synth regenerates it in development) | As row 10. **[recommendation]** Store and show a redacted copy by default. | `subject_ids` = `{candidate_id}` |
| 13 | Contract reviews `legal.reviews` | Clause findings with quotes from the contract (may name counterparty staff); requester id | L3–L4 | Indefinite. Immutable; survives `synth` (no foreign key on purpose). | **None** | **[recommendation]** As long as the contract plus the limitation period for contract claims **[counsel]**; delete reviews of pasted or uploaded text that never became a contract after 12 months | `requested_by`, `contract_id`. **`subject_ids` is left empty** by `legal.save_review()`. |
| 14 | Metric snapshots `metrics.snapshots` | Aggregates only (CHECK `cardinality(subject_ids) = 0`; groups under 5 suppressed); `signed_by`, `published_by` user ids | L2–L4 | Indefinite; signed rows frozen, changes become new versions | **None** (not needed for the aggregates) | Keep as management records (company policy). Not accounting records: those are the `fin.*` tables, kept 7 years under the Bookkeeping Act (1999:1078) ch. 7 s. 2 | — |
| 15 | Egress log `ops.egress_log` | User id, run id, level and **full payload** of every external model call (L1/L2 by CHECK) | ≤ L2 | Indefinite | **None** | **[recommendation]** Replace `payload` with its hash after 90 days; delete rows after 12 months | `user_id`, `run_id` |
| 16 | Budget reservations `ops.budget_reservations` (`ops.budgets` holds department totals only) | User id, run id, token counts | — | Indefinite | **None** | **[recommendation]** 90 days | `user_id` |
| 17 | Sessions `core.sessions` | Session id, user id, created, expires, revoked | — | Indefinite (8-hour lifetime; the row stays). `synth` truncates it. | **None** in production paths | **[recommendation]** Delete 30 days after expiry or revocation | `user_id` |
| 18 | Directory `core.users`, `core.memberships`, `core.grants` | Staff identity, role, manager, clearance; group memberships; time-boxed grants with reason and grantor | — | Follows the HR record; leavers are deactivated, not deleted (issues-log D-12) | Deactivation (`active = false`); live grants expire by `expires_at` | **[recommendation]** Delete a leaver's account data when the HR record's retention ends; keep the user id, which the audit log references | `user_id` |
| 19 | Person-name gazetteer (process memory) | Names of staff, CRM contacts and candidates, used to classify text as L3 | — | While the process runs | **[implemented]** The runner compares `ops.name_source_fingerprint()` before every run and reloads when the source names change ([corpai/agents/_gazetteer.py](../corpai/agents/_gazetteer.py)), so a deletion at the source reaches it before the next run | As implemented | — |
| 20 | Embedding model cache `.cache/fastembed` | None (model weights) | — | Until removed | — | — | — |
| 21 | Report exports | Report text, exporter's id and name in the watermark | report level | Not stored on the server: returned to the user. The `export` audit row stays. | n/a | Users follow the document-handling policy for the level shown in the watermark | — |
| 22 | Demo recordings `ui/fixtures/*.js` | Synthetic only (fictional personas, résumés, contracts, audit rows) | synthetic L1–L4 | In git | Overwritten by `python -m corpai demo-record`, which records from its own throwaway synthetic database and deletes it afterwards ([corpai/cli/demo_record.py](../corpai/cli/demo_record.py)) | **[recommendation]** Never record from a database with real data | — |
| 23 | Business tables (`crm.*`, `eng.*`, `legal.*`, `fin.*`, `hr.employees`, `hr.compensation`, `hr.performance`, `hr.requisitions`) | Systems of record, simulated | L2–L4 | Until the next `synth` run (development) | `synth` truncates and rewrites them (`TRUNCATE … CASCADE`, [corpai/synth/writer.py](../corpai/synth/writer.py)) | In production these are the CRM, HRIS, ERP and contract systems, with their own retention. The AI platform reads them; it should not copy them. | `subject_ids` on CRM contacts and interactions and on HR rows |

## 2. What "implemented" covers

- **Checkpoint TTL.** Real deletion (SQL `DELETE` as the owner) of everything a conversation stored, with an audit row. Two limits: someone has to run it, and "idle" is measured from the newest checkpoint, so a thread that is used every few weeks keeps its whole history.
- **Upload TTL.** Real, in memory only.
- **Trace erasure by subject.** `trace.erase_subject(id)` is a real owner-side function. It covers what tool and retrieval spans read, but not the prompts of model calls made by domain code. Nothing calls it yet except a person running the SQL.
- **Gazetteer refresh.** Follows the sources automatically.
- **`synth` and `db reset`** wipe tables, but they are development tools, not retention.
- **RAG versioning** stops retrieval of old versions. It does not delete them.

Everything else in the table is **not implemented**.

## 3. Data subject requests

There is no tooling for requests; the privacy team would work from owner-side SQL. The label column `subject_ids` (architecture §4) is the main index and is filled on CRM contacts and interactions, HR rows, candidates, screening evidence and decisions, proposals, the outbox and corpus documents that name people. User ids index everything a member of staff did.

### Who can be found where

| Data subject | Key | Found in | Not findable |
|---|---|---|---|
| Employee (as a user) | `user_id` | `audit.log.actor`, `trace.spans.user_id`, `ops.threads.owner_id` (→ checkpoints), `ops.proposals.requester_id`/`approver_id`, `ops.egress_log.user_id`, `legal.reviews.requested_by`, `hr.screenings.decided_by`, `core.sessions` | Mentions of the employee in other people's free text |
| Employee (as a subject: HR records, documents) | `user_id` in `subject_ids` | `hr.employees`, `hr.compensation`, `hr.performance`, `rag.*` documents that list them; `trace.spans` of tool and retrieval reads | Model-call spans of domain code, checkpoints, egress payloads |
| Business contact | `contact_id` in `subject_ids` | `crm.contacts`, `crm.interactions`, `ops.proposals`, `ops.outbox`, `rag.*` account plans; `trace.spans` of CRM tool reads | Checkpoints, `legal.reviews`, egress payloads |
| Candidate | `candidate_id` in `subject_ids` | `hr.candidates`, `hr.screening_evidence`, `hr.screenings`; `trace.spans` of `hris.get_candidate`/`list_candidates` reads; audit `resource = 'hr.candidate:<id>'`, whose `run_id` leads to the screening thread and its spans | The HR screening `llm` spans (redacted résumé, L3-masked), which record no candidate id |

### Access (Art. 15)

- **Today [partial].** Owner-side queries over the keys above, by hand. The candidate's evidence and decision rows also give the explanation that AI Act Art. 86 asks for ([dpia-hr-screening.md](dpia-hr-screening.md) §6).
- **[recommendation]** An owner job, for example `python -m corpai dsar export --subject <id>`, that collects every row with the id in `subject_ids` or a user-id column, drops other people's data, writes one file and is audited as `owner_job` `dsar`.

### Erasure (Art. 17)

- **Today [partial].** Checkpoints disappear after the TTL once prune runs. Everything else needs manual owner SQL, in foreign-key order; for a candidate that means `hr.screenings` and `hr.screening_evidence` before `hr.candidates`.
- **Exceptions.** `audit.log` keeps ids and metadata, not content, and is needed for security and accountability and to establish or defend legal claims (Art. 17(3)(b) and (e); AI Act Art. 26(6) for the HR system). It is kept until its retention period ends (row 5), then removed with its segment. The same applies, for the limitation period, to HR decisions under a pending or possible discrimination claim. **[counsel]**
- **Traces.** Run `SELECT trace.erase_subject('<id>')` as the owner. For a candidate, also blank the spans of the screening runs found through the audit trail (`run_id`), because those model-call spans do not record the candidate id.
- **Gaps.**
  - Spans of model calls made by domain code record no subject ids. Recommendation: the domain functions add the subjects they read to their span, as `call_tool` and `retrieve` do.
  - `legal.reviews` has empty `subject_ids`.
  - Checkpoints and `ops.egress_log` payloads are not indexed by data subject. The short TTL is the mitigation for checkpoints; the payload hashing in row 15 would be the one for the egress log.
- **[recommendation]** An owner job `dsar erase --subject <id>` covering rows 1, 4, 7–13 and 15 (calling `trace.erase_subject` for row 7), audited, and refusing to run while a legal hold flag is set.

### Objection (Art. 21)

- **Direct marketing (sales follow-ups).** Absolute. **Not implemented:** no `do_not_contact` flag and no suppression check in `crm.send_email` or its executor ([lia-sales-profiling.md](lia-sales-profiling.md) S7).
- **AI-assisted CV screening (based on Art. 6(1)(f)).** **Not implemented:** there is no way to record a decision without running the AI step ([dpia-hr-screening.md](dpia-hr-screening.md) R12).
- **Q&A, reports, audit, traces.** An objection by an employee is weighed case by case. The heads see only aggregates of at least 5, so objections about being monitored are mainly answered by the design (decision D7).

### Rectification and restriction (Art. 16, 18)

- **Rectification.** Fix the system of record. Derived stores follow: RAG on the next ingest (new version), screening by running it again (new evidence row; the decision keeps what was seen), the gazetteer through its fingerprint check.
- **Restriction.** No mechanism. **[recommendation]** Narrow the row's ACL to the data owner (for example `head:hr` only) and mark it, since every reader goes through RLS.

### Leavers

Deactivation takes effect on the next request: the principal is re-read every time (architecture §5). The leaver's open threads stay paused and unreadable, because RLS on checkpoints needs an active owner, until prune removes them after 30 idle days. Their traces and audit rows stay under the periods in rows 5 and 7.
