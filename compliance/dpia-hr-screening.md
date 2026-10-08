> **Fictional company, synthetic data. Illustrative, not legal advice.**

# DPIA: AI-assisted CV screening (`hr_screening`)

Data protection impact assessment under GDPR Art. 35, with the EU AI Act assessment for the same system (§8).

| | |
|---|---|
| Version | 0.1 (draft), 2026-10-08 |
| System | App `hr_screening`: [corpai/agents/hr/screening.py](../corpai/agents/hr/screening.py), [corpai/agents/hr/decision.py](../corpai/agents/hr/decision.py), [corpai/hr/](../corpai/hr/), [corpai/classify/protected.py](../corpai/classify/protected.py), `migrations/044_synth_hr.sql`, `migrations/090_m4_hr_screening.sql` |
| Business owner | Head of People (Maria Engström, u0050) |
| Assessed with | Head of Legal (Sofia Wallin, u0030); platform administrator (Daniel Sjögren, u0060) |
| DPO advice | No DPO appointed (see [README.md](README.md)) |
| Employee representatives | Not yet consulted (§7) |
| **Outcome** | **Not approved for real candidates in the current build.** Residual risk is high until the measures in §9 marked *before go-live* are done, the first of which is moving the `local` route off a third-country API. |

Status markers: [README.md](README.md).

## 1. Why a DPIA is needed

The processing meets several of the criteria for likely high risk in the DPIA guidelines of the Article 29 Working Party (WP248 rev.01, endorsed by the EDPB), and two are normally enough. IMY's Art. 35(4) list of processing that needs a DPIA builds on the same criteria.

- **Evaluation of personal aspects.** The system judges a candidate against job requirements. That is profiling (Art. 4(4)).
- **Vulnerable data subjects.** Applicants depend on the employer's decision and are in a weak position to object.
- **New technology.** A large language model reads the résumé.
- **Sensitive data is likely to be present.** Résumés often carry photos, dates of birth, family status and health remarks. The synthetic ones do too.
- **Decisions about access to a contract.** The output feeds the decision on whether a candidate moves forward.

Under the AI Act, AI systems used "to analyse and filter job applications, and to evaluate candidates" are high-risk (Annex III point 4(a)). A deployer has to use the provider's Art. 13 information for its DPIA (Art. 26(9)). See §8.

## 2. Systematic description

### 2.1 Purpose and scope

Help a recruiter check one candidate's résumé against the hard requirements of one requisition, consistently and with quoted evidence, so that a person can decide quickly and explain the decision. Decision D6 fixes the design: **the AI only extracts evidence**, per requirement *met / not met / unknown* with a verbatim quote. It gives no total score, no ranking, no recommendation and no automatic rejection; a person decides.

Out of scope: interviews, reference checks, background checks, offers, internal promotion.

### 2.2 Data flow (as implemented)

```text
candidate applies ──► hr.candidates (L3, trust 'untrusted', ACL req:<id> + head:hr)
                                     │
HR user (HR staff on the requisition, or head of HR) opens #/hr, enters "CAND-…"
                                     │  POST /api/runs {app: hr_screening}
                                     ▼
intake  (node caps {sensitive_data})
   picks the CAND id with a regex; the input text never reaches a model or a query;
   raises the session to L3 before anything is read (own node, so the raise is checkpointed)
                                     ▼
screen  (caps {untrusted_input, sensitive_data}; no tools of any kind)
   corpai.hr.screen_candidate(principal, candidate_id):
   1. policy.tool_allowed(hr_screening, hris.get_candidate), else audit hr_screening deny
   2. read candidate + requisition under RLS (pg.principal_tx); not visible, so audit deny
   3. prepare_resume(): drop markup; replace name, e-mail, phone; remove URLs and @handles;
      remove phone-shaped strings; classify.redact_protected() removes protected attributes
   4. per hard requirement: ModelGateway.chat(purpose hr.screen, hwm L3, json_mode,
      temperature 0, max_tokens 300, tools None), so the local route
   5. parse_model_verdict(): anything off-schema becomes unknown; extra keys dropped and flagged
   6. check_quote(): met / not_met stand only on a verbatim quote of at least 12 characters and
      2 real words that lies outside every embedded-instruction span; otherwise unknown
   7. hr.write_screening_evidence() (SECURITY DEFINER): L4 row, ACL req:<id> + head:hr,
      CHECK model_route = 'local', CHECK no score / rank / recommendation / decision keys
   8. audit hr_screening allow (metadata: requirement count, verdict counts, flags)
   session raised to L4
                                     ▼
decide  (gate; interrupt {"type": "hr_decision"}), only if evidence was stored
                                     │
HR user reads the evidence and decides: POST /api/hr/screenings/decision
   {thread_id, candidate_id, decision: advance|hold|reject, reason}
   corpai.hr.record_decision(): reason 1–2000 chars required (Python and SQL), audited hr_decision
   hr.record_screening_decision() (SECURITY DEFINER): actor from the session, hr._may_screen(),
   copies the latest evidence items into hr.screenings (what the person saw)
                                     ▼
record  reports the decision; the reason text never enters graph state or checkpoints
```

`record_decision` is not an MCP tool and no graph node can call it (`tests/hr/test_decisions.py::test_no_model_tool_can_record_a_decision`).

### 2.3 Data inventory

| Data | Where | Level | Who can read it | Reaches the model? |
|---|---|---|---|---|
| Candidate name, e-mail, phone | `hr.candidates` | L3 | `req:<id>` holders, head of HR | No: replaced before the model (`prepare_resume`) |
| Résumé text (raw, may contain protected attributes and hidden instructions) | `hr.candidates.resume_text` | L3, untrusted | `req:<id>` holders, head of HR | Only the redacted version |
| Hard requirements (HR-authored) | `hr.requisitions.hard_requirements` | L2 | HR, heads, panel | Yes |
| Evidence per requirement (verdict, quote, flags) | `hr.screening_evidence.evidence` | L4 | HR staff on the requisition, head of HR | It is the model's output |
| Decision, reason, decider, time, copy of the evidence seen | `hr.screenings` | L4 | HR staff on the requisition, head of HR | No |
| Run state (candidate id, evidence, decision outcome) | LangGraph checkpoints (`lg.*`) | L4 session | The HR user who ran it | No |
| Model prompts and replies (redacted résumé, requirement, verdict JSON) | `trace.spans`, `llm:hr.screen` spans, text masked at L3 (dropped if the text classifies as L4) | L3 | The HR user who ran it (own spans); platform admin metadata only | It is the prompt |
| Audit rows `hr_screening`, `hr_decision` | `audit.log` | metadata | Security auditor | No |
| Model prompts (in transit) | Gateway, then the provider | L3 | Provider (today DeepSeek) | Yes |

The hiring manager does **not** see the evidence or the decision: `hr._may_screen()` admits only HR staff on the requisition and the head of HR, and `tests/hr/test_screening.py::test_hiring_manager_may_not_screen` checks it. The manager sees the candidate list and raw résumés through `hr_candidates` ([ropa.md](ropa.md) activity 7).

### 2.4 Volumes and context

About 70 staff; six requisitions in the synthetic H2 2026 plan ([docs/company.md](../docs/company.md)); a few hundred applications a year. Candidates are mostly in the EU. The company is in Sweden and the sales role R-03 is aimed at German-speaking markets.

## 3. Necessity and proportionality

| Principle | Assessment | Status |
|---|---|---|
| Lawfulness (Art. 6) | 6(1)(b) for handling an application the candidate sent; 6(1)(f) for the AI-assisted evidence step. The interest is consistent screening with less manual reading. It is balanced by the measures in §5 and the candidate's right to object (§6). | **[counsel]** confirm |
| Art. 9 | Not relied on. The design keeps special-category data away from the model and from the decision. Raw résumés that contain such data are stored, which is covered only as far as Art. 9(2)(b) with the Swedish Data Protection Act (2018:218) ch. 3 s. 2 reaches. | **[counsel]** |
| Art. 22 | Not a decision based solely on automated processing: a named HR person decides with a written reason, sees the evidence and the quotes, and can decide against them. There is no score that could become the decision in practice (cf. CJEU case C-634/21 of 7 December 2023 on credit scoring, where a score that plays a determining role counted as an automated decision). | [implemented] |
| Purpose limitation | Evidence is tied to one candidate and one requisition (ACL `req:<id>`); it is not offered to other requisitions or to the hiring manager. | [implemented] |
| Minimisation | Name, contact details, links, handles and protected attributes are removed before the model; the model gets one requirement at a time; flags record only the *kinds* removed, never values, and the audit log only their number. | [partial] (R11: pattern-based) |
| Accuracy | Verbatim-quote rule; *unknown* whenever the model cannot point to text; the human reads the quotes. | [partial] (R4) |
| Storage limitation | No retention period is implemented for candidates, evidence or decisions. | [recommendation] (R7) |
| Transparency | No candidate notice yet. | [recommendation] (R6) |
| Security | RLS, SECURITY DEFINER writers that take the actor from the session, L4 labels, audit, Rule of Two. | [implemented] |
| Processors and transfers | The model call is meant to stay on-prem (`local`). Today it goes to DeepSeek. | Not acceptable for real data (R9) |

**Alternatives considered.** Fully manual screening (the baseline; slower and less consistent between recruiters). A model that scores or ranks candidates was rejected in decision D6: it is the riskiest form under Art. 22 and the AI Act and the hardest to explain. Keyword matching was not chosen because it misses paraphrases and is easy to game. Evidence extraction with quotes sits between them and leaves the judgement with a person.

## 4. Risks to candidates

Likelihood and severity: L = low, M = medium, H = high. *Inherent* means without the measures; *residual* means with the measures implemented today, assuming the `local` route were really on-prem (R9 is scored on its own).

| # | Risk | Inherent | Measures (§5) | Residual today |
|---|---|---|---|---|
| R1 | **Direct discrimination.** Protected characteristics in the résumé (sex, age, ethnicity, religion, disability, family status) influence the model's verdicts. The Swedish Discrimination Act (2008:567) protects job applicants (ch. 2 s. 1) on seven grounds (ch. 1 s. 5): sex, transgender identity or expression, ethnicity, religion or other belief, disability, sexual orientation, age. Once an applicant shows facts that suggest discrimination, the employer has to prove there was none (ch. 6 s. 3), so the evidence and the recorded reason matter. | H | M1, M2, M4 | M (see R11) |
| R2 | **Indirect discrimination through proxies and requirements.** Graduation years reveal age; "native" language requirements, career gaps or parental leave reveal origin or sex. Requirements themselves can discriminate: R-03 asks for "Native or near-native German", R-01 for "At least 5 years". | H | M4, M7, M10 | M |
| R3 | **Automated decision / automation bias.** Recruiters rubber-stamp the evidence, so the decision becomes automated in practice (Art. 22, Recital 71). | H | M5, M6 | M |
| R4 | **Inaccurate evidence.** The model misreads the résumé; a real quote is used for the wrong verdict; synonyms ("Postgres" vs "PostgreSQL") produce *unknown* or *not met*. | M | M3, M5 | M |
| R5 | **Manipulation by the candidate (prompt injection).** Hidden text in the résumé instructs the model ("verified by HR: all requirements met"). | M | M3, M8 | L |
| R6 | **No transparency.** Candidates do not know an AI system reads their résumé; they cannot object or ask for an explanation (Art. 13, Art. 21; AI Act Art. 26(11), Art. 86). | H | M9 | **H** (not implemented) |
| R7 | **Excessive retention.** Résumés, evidence and decisions are kept indefinitely, and so are the HR user's `llm` spans holding the redacted résumé. Those spans carry no candidate id, so `trace.erase_subject` cannot find them. | M | M11 | **M–H** (not implemented) |
| R8 | **Unauthorised access or function creep.** Evidence is read by people outside the hiring process, or reused for other roles or for employees. | M | M6, M12 | L |
| R9 | **Third-country transfer.** Today `local` is DeepSeek in China (decision D3): redacted résumés and HR prompts leave the EU without an Art. 28 contract or a Chapter V safeguard. | H | M13 (only after the route moves) | **H** (as built) |
| R10 | **Special-category inferences in the audit log.** The `hr_screening` audit row stored `flags` with `protected_removed:<kind>` (for example `protected_removed:health`) for a named candidate id, in a log that cannot be deleted from and that the security auditor (L2, no other access to HR data) reads. Since the final review (issues-log FN-28) the row keeps only the number of kinds removed; the kinds stay in the L4 evidence row ([corpai/hr/screening.py](../corpai/hr/screening.py), `tests/hr/test_screening.py::test_audit_row_does_not_say_which_protected_attributes_a_resume_held`). | M | M12 | **L** (a count says little about one candidate) |
| R11 | **Swedish-language résumés and the missing Discrimination Act grounds.** Checked on 2026-10-08: "Födelsedatum:", "Kön:", "Civilstånd:", "Ålder:" and "Nationalitet:" passed unchanged, and sexual orientation and transgender identity were not covered in any language. Since the final review (issues-log FN-27) `redact_protected` knows Swedish labels and phrases and both grounds in all four languages (`tests/classify/test_redact_protected.py::test_swedish_cv`, the Swedish counterfactual pairs in `tests/hr/test_prepare.py` and `evals/hr`). It stays pattern-based: a résumé in another language, or wording the patterns miss, reaches the model as written, and the Swedish patterns are proven by tests only (the synthetic résumés are English, German and Chinese). For a Stockholm employer Swedish is the common case. | H | M2 | **M** |
| R12 | **The right to object has no path inside the platform.** A candidate who objects to AI-assisted screening (Art. 21(1)) cannot be decided on inside the platform without running the AI step first: `/api/hr/screenings/decision` needs a thread waiting on evidence for that candidate. | M | — | M |

## 5. Measures

| # | Measure | Code | Evidence | Status |
|---|---|---|---|---|
| M1 | **Identity removal.** Name in every common spelling (case, diacritics, handles, "Last, First", initials), e-mail, phone, URLs and @handles are replaced before the model, so a counterfactual pair gives byte-identical input. | [corpai/hr/prepare.py](../corpai/hr/prepare.py) | `tests/hr/test_prepare.py`, `tests/hr/test_screening.py::test_model_input_has_no_protected_attributes_or_identity` | [implemented] |
| M2 | **Protected-attribute removal.** Photo, gender (including honorifics and English and Swedish pronouns), age, date of birth, ethnicity, nationality, origin, marital status, children, religion, health, disability, sexual orientation, transgender identity, in EN/DE/ZH/SV and through hidden or full-width characters. The *kinds* removed are shown to HR ("protected_removed" notice). | [corpai/classify/protected.py](../corpai/classify/protected.py), [corpai/classify/normalise.py](../corpai/classify/normalise.py) | `tests/classify/test_redact_protected.py`, `tests/classify/test_robustness.py` | [implemented] (patterns; R11 residual) |
| M3 | **Evidence, not opinion.** One requirement per call; a fixed schema; *met / not met* only with a verbatim, substantive quote outside injected passages; anything else becomes *unknown*. | [corpai/hr/evidence.py](../corpai/hr/evidence.py), [corpai/hr/screening.py](../corpai/hr/screening.py) | `test_evidence_is_local_verbatim_and_scoreless`, `test_unverifiable_answers_become_unknown` | [implemented] |
| M4 | **No score, rank or recommendation anywhere.** The pydantic model has no such fields; the evidence table CHECK refuses `score`, `total_score`, `rank`, `ranking`, `recommendation`, `decision`, `reject`, `rejected`. | `migrations/090_m4_hr_screening.sql` | `test_screening_evidence_model_has_no_score_fields`, `test_writer_refuses_scores_and_external_routes` | [implemented] |
| M5 | **Human decision with a reason.** advance / hold / reject only; reason 1–2000 characters (Python and SQL); `decided_by`/`decided_at` required by a table CHECK; the evidence the person saw is copied into the decision row. | [corpai/hr/decisions.py](../corpai/hr/decisions.py), `hr.record_screening_decision()` | `tests/hr/test_decisions.py`, `tests/m4/test_hr_flow.py::test_decision_needs_reason_role_own_thread_and_the_right_candidate` | [implemented] |
| M6 | **Who may screen and decide.** HR staff holding `req:<id>` (clearance L4, dept hr) or the head of HR, checked in SQL by role **and** group **and** department, so a forged session variable fails. | `hr._may_screen()` in migration 090 | `test_only_hr_may_screen`, `test_hr_staff_only_on_their_requisitions`, `test_sql_layer_refuses_without_python_checks` | [implemented] |
| M7 | **Counterfactual and group-difference evaluation.** Planted pairs CF-1…CF-3 differ only in name, gender and date of birth. With the fake model, the prompts and verdicts must be identical. With the live model at most one requirement per pair may differ, and the met-rate per gender and age band is reported (groups of at least 5 compared). | [evals/hr/test_counterfactual_eval.py](../evals/hr/test_counterfactual_eval.py), [corpai/hr/bias.py](../corpai/hr/bias.py) | `tests/hr/test_screening.py::test_counterfactual_pairs_get_identical_input_and_verdicts`, `tests/m4/test_hr_flow.py::test_counterfactual_pairs_get_identical_evidence_through_the_graph` | [implemented] (fake model); live runs are part of issues-log D-07 |
| M8 | **Prompt-injection containment.** The résumé is untrusted; the screen node holds no tool at all (Rule of Two); embedded instructions are flagged, and quotes from them do not count; tool calls in the reply are ignored and flagged. | [corpai/sales/untrusted.py](../corpai/sales/untrusted.py), [corpai/agents/_rule_of_two.py](../corpai/agents/_rule_of_two.py) | `test_injected_resume_instruction_has_no_effect`, `test_obedient_model_cannot_use_the_injection`, `tests/security/test_rule_of_two.py` | [implemented] |
| M9 | **Transparency to candidates.** A notice on the careers page and in the acknowledgement: AI-assisted evidence extraction, no automated decision, the logic in plain words, the right to object and to ask for an explanation, the retention period, contact. | none | — | [recommendation] |
| M10 | **Requirement review.** HR and the hiring manager confirm that each hard requirement is necessary for the job and worded neutrally ("fluent German (C1)" instead of "native"; experience as skills, not years, unless the years are justified). Done before a requisition opens and documented in the Discrimination Act's active-measures work (ch. 3; employers with 25 or more staff document it in writing every year). | none | — | [recommendation] |
| M11 | **Retention.** Delete résumés, evidence, decisions and screening checkpoints at a fixed time after the recruitment closes, for example 2 years to be able to answer a discrimination claim; for the hired candidate, move what the personnel file needs and delete the rest. | none (checkpoints only: `python -m corpai prune`) | — | [recommendation] **[counsel]** for the period |
| M12 | **Audit trail.** Every screening attempt (`hr_screening`) and every decision (`hr_decision`), allowed or refused, gets its own audit event; the reason text is kept out of the audit row. | [corpai/audit/log.py](../corpai/audit/log.py) `EVENTS` | `tests/audit/test_events.py` | [implemented] |
| M13 | **Model stays in ABC Tech's control.** Point `local` at an on-prem or EU-controlled model before real data; the evidence table already refuses any route but `local`. | [config/models.yaml](../config/models.yaml), migration 090 CHECK | — | [recommendation] |
| M14 | **Training and oversight for recruiters.** What the evidence means and does not mean; read the quotes, not only the verdicts; when to decide against the evidence; spot checks of decisions by the head of HR. AI Act Art. 4 (AI literacy) and Art. 26(2). | none | — | [recommendation] |

Measures that would close the open findings (R10 and most of R11 were done in the final review; the rest are not implemented):

- **R10:** done in the final review (the audit details hold a count only; FN-28).
- **R11:** labels, phrases and the two grounds done in the final review (FN-27). Still open: Swedish résumés in the synthetic data, so the live counterfactual eval covers them too.
- **R12:** a "decide without AI" path: either a decision route that does not require evidence, or a per-candidate `no_ai_screening` flag that `screen_candidate` refuses to override.

## 6. Data subject rights

| Right | How it works today | Status |
|---|---|---|
| Information (Art. 13) | No candidate notice | [recommendation] (M9) |
| Access (Art. 15) | The privacy team can compile the candidate's row, evidence and decision with an owner query (`subject_ids @> '{CAND-…}'` on `hr.candidates`, `hr.screening_evidence`, `hr.screenings`). No tooling | [partial] ([retention-matrix.md](retention-matrix.md) §3) |
| Explanation (AI Act Art. 86; Art. 15(1)(h) where it applies) | The decision row holds the requirement texts, verdicts, quotes and the human reason, which is enough for a meaningful explanation | [implemented] (data); [recommendation] (procedure) |
| Rectification (Art. 16) | Correct the résumé, then screen again; a new evidence row is written and the old one is kept | [partial] |
| Erasure (Art. 17) | Manual owner deletion, in foreign-key order (`hr.screenings` and `hr.screening_evidence` first, then `hr.candidates`). Then the screening thread and its trace spans, both found through the audit row's `run_id` → `ops.thread_runs`; `trace.erase_subject` finds them only once the screening span records the candidate id. Audit rows stay (Art. 17(3)(b)/(e)) | [recommendation] (no job) |
| Objection (Art. 21(1)) | No path to decide without the AI step | [recommendation] (R12) |
| Not to be subject to a solely automated decision (Art. 22) | A person decides; no score exists | [implemented] |

## 7. Consultation

**Employee representatives (Sweden).** Introducing AI-assisted screening changes how HR staff and hiring managers work and brings in a new kind of control over applicants, some of whom are existing employees applying internally. Before deciding to introduce it:

- If ABC Tech is bound by a collective agreement, it must negotiate with the union on its own initiative before deciding on an important change: the Co-determination Act (MBL, 1976:580) §11, primary negotiation. If it is not bound by one, §13 requires negotiation on matters that particularly concern union members' conditions. Whether ABC Tech is bound by a collective agreement is not settled in the company script. **[counsel]**
- MBL §19: keep the unions informed on an ongoing basis.
- AI Act Art. 26(7): before putting a high-risk AI system into service at the workplace, an employer informs the workers' representatives and the affected workers.
- Record of consultation: none yet. **[recommendation]** Hold the negotiation with this DPIA, the evaluation results (M7) and the requirement-review rule (M10) on the table, and record the outcome here.

**Candidates (Art. 35(9)).** Where appropriate, seek the views of data subjects: a short test of the candidate notice and the explanation with a few recent applicants. **[recommendation]**

**DPO and IMY.** No DPO to consult. If the residual risk stays high after §9, ABC Tech must consult IMY before starting (Art. 36).

## 8. EU AI Act

**Classification.** The system filters and evaluates job applications, so it is high-risk under Annex III point 4(a). The Art. 6(3) exception for "narrow procedural" or "preparatory" tasks cannot be used: Art. 6(3) says an Annex III system that profiles natural persons is always high-risk, and checking a candidate against requirements is profiling. **[counsel]** confirm.

**When.** Under Regulation (EU) 2024/1689 as published, the Annex III obligations apply from 2 August 2026, and AI literacy (Art. 4) and the prohibitions (Art. 5) from 2 February 2025. The Commission's 2025 "digital omnibus" proposal would postpone the Annex III obligations. Whether and how that was adopted must be checked against the Official Journal **[counsel]**. This assessment plans as if the obligations already apply.

**Prohibited practices.** None: no emotion recognition at the workplace or in recruitment (Art. 5(1)(f)), no biometric categorisation. Photos are removed, not analysed.

### Deployer obligations (Art. 26 and related)

| Obligation | What it means here | Status |
|---|---|---|
| Art. 26(1): use the system according to its instructions for use | Written instructions for recruiters: one candidate, one requisition; read the quotes; the evidence is not a recommendation | [recommendation] |
| Art. 26(2): human oversight by competent, trained people with authority | Only HR staff on the requisition and the head of HR can screen and decide (M6); training (M14) | [partial] |
| Art. 26(4): input data relevant and representative, as far as the deployer controls it | The hard requirements are the deployer's input: requirement review (M10) | [recommendation] |
| Art. 26(5): monitor operation; on a risk or serious incident, stop using the system and inform the provider and the authority | Periodic review of verdict distributions and the group-difference report; an incident procedure | [recommendation] |
| Art. 26(6): keep the automatically generated logs for at least six months | `audit.log` (`hr_screening`, `hr_decision`, `model_call`), `hr.screening_evidence`, `trace.spans`; kept indefinitely today | [implemented] (kept); retention to be set (≥ 6 months) |
| Art. 26(7): inform workers' representatives and affected workers before putting the system into service | §7 | [recommendation] |
| Art. 26(9): use the provider's Art. 13 information for the DPIA | This document, built from the code | [partial] |
| Art. 26(11): inform people that they are subject to a high-risk AI system that assists decisions about them | Candidate notice (M9) | [recommendation] |
| Art. 86: explanation of the role of the AI system in an individual decision, on request | Decision row holds evidence and reason (§6) | [partial] |
| Art. 4: AI literacy of staff who use the system | Training (M14) | [recommendation] |
| Art. 27: fundamental rights impact assessment | Not required: ABC Tech is a private employer, not a public body or a provider of public services. This DPIA covers most of the same ground | n/a |

### Is ABC Tech also the provider?

Probably yes. ABC Tech built the screening system itself and puts it into service for its own use. Under Art. 3(3) and 3(11) that makes it the **provider** as well as the deployer, whatever model sits underneath (the general-purpose model's provider has its own, separate obligations). **[counsel]** If confirmed, ABC Tech also needs, before putting the system into service:

- a risk management system and data governance (Arts. 9–10);
- technical documentation (Art. 11, Annex IV), automatic logging (Art. 12; partly present through the audit log), instructions for use (Art. 13), human oversight by design (Art. 14; largely present: evidence only, human decision), accuracy, robustness and cybersecurity (Art. 15);
- a quality management system (Art. 17) and documentation kept for 10 years (Art. 18);
- conformity assessment by internal control (Art. 43(2), Annex VI), an EU declaration of conformity (Art. 47) and CE marking (Art. 48);
- registration in the EU database (Art. 49);
- post-market monitoring (Art. 72) and serious-incident reporting (Art. 73).

## 9. Action plan

| # | Action | Owner | When | Status |
|---|---|---|---|---|
| A1 | Point `local` at a model ABC Tech controls (R9, M13) | Platform admin | Before go-live | Open |
| A2 | Swedish labels and the missing Discrimination Act grounds in `redact_protected`; Swedish counterfactual pairs (R11) | Platform team | Before go-live | Done in the final review (pairs in tests and `evals/hr`, not yet in the synthetic data) |
| A3 | Remove protected-attribute kinds from the `hr_screening` audit details (R10) | Platform team | Before go-live | Done in the final review |
| A4 | Candidate notice and explanation procedure (R6, M9) | Head of People with Legal | Before go-live | Open |
| A5 | Retention periods and a deletion job for candidates, evidence, decisions (R7, M11) | Head of People; platform team | Before go-live | Open |
| A6 | MBL negotiation and AI Act Art. 26(7) information (§7) | Head of People | Before go-live | Open |
| A7 | Requirement review rule (M10); fix "native" wording in R-03 | Head of People; hiring managers | Before go-live | Open |
| A8 | Decide-without-AI path (R12) | Platform team | Before go-live | Open |
| A9 | Live counterfactual and group-difference evaluation with the on-prem model; record results (M7) | Platform team | After A1 | Open (issues-log D-07) |
| A10 | Recruiter training and instructions for use (M14, Art. 26(1)–(2)) | Head of People | Before go-live | Open |
| A11 | Settle the provider question and the AI Act timeline (§8) | Head of Legal | Before go-live | Open |
| A12 | Redacted résumé by default in the hiring manager's candidate page (the bias notice is done: issues-log WR-17) | Platform team | Before go-live | Partly done |
| A13 | Record the candidate id on the screening span, so `trace.erase_subject` reaches the prompts; a TTL for trace text (R7) | Platform team | Before go-live | Open |

## 10. Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Business owner | | | |
| Head of Legal | | | |
| Employee representatives (consulted) | | | |
| Review due | 12 months after go-live, or at any change to the model, the prompts or the redaction | | |
