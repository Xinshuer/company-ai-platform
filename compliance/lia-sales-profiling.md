> **Fictional company, synthetic data. Illustrative, not legal advice.**

# Legitimate interests assessment: B2B prospect research and follow-up (`sales_profile`)

GDPR Art. 6(1)(f), with the information duty (Art. 14), the right to object (Art. 21) and the e-mail marketing rules that sit next to them.

| | |
|---|---|
| Version | 0.1 (draft), 2026-10-08 |
| System | Apps `sales_profile` and its sub-agent `sales_web`: [corpai/agents/sales/graph.py](../corpai/agents/sales/graph.py), [corpai/agents/sales/web.py](../corpai/agents/sales/web.py), [corpai/sales/](../corpai/sales/), [corpai/mcp_servers/web.py](../corpai/mcp_servers/web.py), [corpai/mcp_servers/web_allowlist.py](../corpai/mcp_servers/web_allowlist.py), `crm.send_email` in [corpai/mcp_servers/crm.py](../corpai/mcp_servers/crm.py) |
| Business owner | Head of Sales (Karin Holm, u0010) |
| Assessed with | Head of Legal (Sofia Wallin, u0030) |
| Outcome | Legitimate interests can support company research and follow-ups to existing business contacts once the safeguards marked *recommendation* in §5 are in place. They do not cover cold e-mail to new contacts in countries that require prior consent (§7). |

Status markers: [README.md](README.md).

## 1. What the processing is

Decision D5: **the platform profiles companies, not people.**

**Profile branch.** A salesperson (or the CEO) enters a company name.
1. `intake` accepts only a plain company name at L1. It becomes the web query.
2. `sales_web`, an isolated sub-agent that holds only `web.search` and `web.fetch`, searches public pages about the company and fetches up to 12 of them. The model then extracts a `CompanyProfile`: industry, headquarters country, countries, fleet-size band, employee band, funding stage, last funding amount, growth and technology signals, plus the company name, domain and source URLs. Every field is an enum, a number, a bounded identifier or a URL from the pages read; there is no free-text field and no field about a person ([corpai/sales/profile.py](../corpai/sales/profile.py)). The sub-graph runs without a checkpointer, so page text is never stored.
3. `score` reads the CRM **as the user** (row-level security): the account, its interactions and its contacts. Code computes *intent* from the interactions (no model, [corpai/sales/intent.py](../corpai/sales/intent.py)) and *fit* from the profile, and picks a playbook tier.
4. `strategy`: the local model writes a short follow-up strategy from the CRM account name, the profile's enum and number fields, the scores with their reasons, and the playbook. The prompt never includes page text, contact data or the profile's company, domain or source fields.

**Follow-up branch.** In the same conversation the salesperson types `/followup [CT-xxxxx]`.
1. `draft_email`: fresh CRM rows and the playbook entry go to the local model, which drafts a subject and body addressed to a CRM contact (name and title). Code rejects any draft with links, e-mail addresses or markup and falls back to a fixed template ([corpai/agents/sales/_email.py](../corpai/agents/sales/_email.py)).
2. `propose_email`: `crm.send_email` files a **tier-3 proposal**. Nothing is sent yet. The recipient must be an existing contact of that account.
3. `wait`: another person (the head of sales or the CEO; never the requester) sees the full e-mail and its parameters hash and approves or rejects.
4. On approval the executor writes the e-mail to `ops.outbox`, a simulated mail system. The recipient must end in `.example`, so nothing can leave.

## 2. Personal data involved

| Data | Source | Data subjects | Level | Stored |
|---|---|---|---|---|
| Company facts (firmographics) | Public pages | Mostly legal persons; personal data only for sole traders or very small firms | L1 | Profile in checkpoints |
| Names or quotes of people on public pages (managing directors in news or funding articles) | Public pages | Business contacts, executives | L1 when published | Not stored (page text never checkpointed); seen by the extraction model |
| Contact name, title, e-mail, phone | CRM (`crm.contacts`) | Business contacts | L3 | CRM (system of record); contact list in checkpoints |
| Interaction history (meetings, calls, notes) | CRM (`crm.interactions`) | Business contacts, sales staff | L3 | CRM; only the intent score and tier in checkpoints |
| Draft and sent e-mail (recipient, subject, body) | Generated | Contact, sender, approver | L3 | `ops.proposals`, `ops.outbox` |
| User ids of salesperson and approver | Platform | Staff | — | Proposals, audit |

The synthetic web corpus names no individuals ([corpai/synth/web.py](../corpai/synth/web.py)). Real company pages often do, so the assessment covers that case.

## 3. Purpose test: is there a legitimate interest?

- **Interest.** Finding fleet operators that could use FleetPilot and following up with people ABC Tech is already talking to. Recital 47 recognises direct marketing as a possible legitimate interest. Keeping in touch with business contacts is an ordinary commercial interest.
- **Lawful and specific.** Yes: B2B sales of a lawful product, to the companies' logistics and operations functions.
- **Benefit.** Less manual research; consistent, documented scoring; follow-ups that match an agreed playbook. The approval gate also protects the contact from careless or manipulated e-mails.

Result: passed.

## 4. Necessity test: is the processing needed for that interest?

- **Company research.** It needs company facts, not facts about people. The query is the company name only, checked by a pattern in the graph and again by the server ([corpai/mcp_servers/web.py](../corpai/mcp_servers/web.py) `_company_query`). The profile holds no person fields. People's names on pages are a by-product: the extraction model reads them but cannot output them.
- **Intent score.** It uses interactions ABC Tech already holds, at account level, computed by code. A less intrusive alternative (asking the salesperson to judge) exists, but the score keeps judgement consistent and needs no new data.
- **Follow-up e-mail.** It needs the contact's name, title and address, all already in the CRM. No enrichment from outside sources.
- **What is not done.** No search for individuals, no social-network scraping, no inference of personal traits, no contact scoring.

Result: passed, provided the allowlist stays limited to company sources (S9).

## 5. Balancing test

| Factor | Assessment |
|---|---|
| Nature of the data | Business role data (name, title, work e-mail) and company facts. No special categories; no data about private life. |
| Reasonable expectations | A contact who has met or written to ABC Tech in a business role expects a follow-up. Executives named in a company's own press releases expect business interest in their company. A contact with no prior relationship expects less (§7). |
| Impact | An e-mail. No legal or similarly significant effect; no automated decision about a person. Wrong company facts affect the company profile, not a person. |
| Power balance | Equal (business to business); the contact can ignore or object. |
| Children or vulnerable people | None. |

### Safeguards

| # | Safeguard | Code | Status |
|---|---|---|---|
| S1 | **Public-only egress for research.** The query is built from the company name only and classified before it leaves (`check_egress("search.mock", …)` at the level of the query or the full URL, never with `public=True`); fetch only `https` pages on allowlisted hosts (the hosts of the mock corpus, `.example` only), no query strings or fragments. | [corpai/mcp_servers/web.py](../corpai/mcp_servers/web.py), [corpai/mcp_servers/web_allowlist.py](../corpai/mcp_servers/web_allowlist.py) | [implemented] |
| S2 | **L1/L2 routing.** Extraction goes to the `external` model only when every prompt field is byte-identical to a published page and the session is L1 (`public_only`, issues-log MR-01); otherwise `local`. Any recognised person name (CRM contacts are in the gazetteer) makes the text L3. Strategy and e-mail drafting always run `local`. | [corpai/sales/extract.py](../corpai/sales/extract.py), [corpai/gateway/gateway.py](../corpai/gateway/gateway.py) | [implemented] |
| S3 | **No person profiling, no special categories.** The profile is a fixed schema of enums and numbers; invalid values are dropped, never repaired with model text; CRM data never enters the extraction; page text never reaches a checkpoint. | [corpai/sales/profile.py](../corpai/sales/profile.py), [corpai/agents/sales/web.py](../corpai/agents/sales/web.py) | [implemented] |
| S4 | **Isolation (Rule of Two).** The web branch holds untrusted input and no outbound action; the follow-up branch holds an outbound action and no untrusted input. The follow-up never reads page content. | `tests/m4/test_sales_flow.py`, `tests/security/test_rule_of_two.py` | [implemented] |
| S5 | **Approval before outreach.** `crm.send_email` is tier 3 (four eyes): never the requester; the head of sales or the CEO. The approver sees the full e-mail and its parameters hash, and the executor re-checks the hash. | [corpai/proposals/](../corpai/proposals/), [corpai/policy/approvals.py](../corpai/policy/approvals.py) | [implemented] |
| S6 | **Known recipients only.** The recipient must be a contact of that account readable by the user; an address from a page or from the model is refused. Drafts may not contain links or addresses. | [corpai/mcp_servers/crm.py](../corpai/mcp_servers/crm.py) `send_email`, [corpai/agents/sales/_email.py](../corpai/agents/sales/_email.py) | [implemented] |
| S7 | **Opt-out and suppression.** A `do_not_contact` flag (date, channel, source of the objection) on `crm.contacts`, checked by `crm.send_email` and again by the executor; a minimal suppression list (for example a hash of the address) that survives deletion of the contact, so an objection is honoured later. | none: `crm.contacts` has no such column and the executor checks nothing | [recommendation] |
| S8 | **Information in every e-mail.** A fixed footer added by code (the model may not write links or addresses): who ABC Tech is, why the contact receives the e-mail, the source of their data if not from them, the right to object with a one-step way to do it, and a link to the privacy notice. | none | [recommendation] |
| S9 | **Source allowlist governance.** In a real deployment the web tool reaches a fixed list of company websites, trade press and public company registers. It never reaches social networks (LinkedIn's terms forbid scraping, design-review §3) or personal profiles. Every profile keeps its source URLs. | allowlist mechanism [implemented]; real list [recommendation] | [partial] |
| S10 | **Retention for contacts and e-mails.** Delete contacts with no interaction for 24 months unless there is a customer relationship; keep outbox records as long as the contact. | none | [recommendation] |
| S11 | **Fewer names in extraction prompts.** Optionally remove person names from page text before extraction. The call would then run `local`, because the text would no longer be byte-identical to the published page. | none | [recommendation] (optional) |

Result: with S1–S6 the interests are balanced for company research and for follow-ups within an existing business conversation. S7 and S8 are required before any real e-mail is sent, because Art. 21(2)–(4) makes the objection absolute and requires it to be offered at the first communication.

## 6. Information duty and the right to object

**Art. 14 (data not obtained from the contact).** This covers contacts added from colleagues, public sources or event lists. Tell them who the controller is, the purposes and the legitimate interest, the categories of data, the source, recipients, transfers, retention and their rights, the right to object above all. If the data is used to communicate with them, tell them at the latest at the first communication (Art. 14(3)(b)); otherwise within a month. The disproportionate-effort exception (Art. 14(5)(b)) does not fit: ABC Tech writes to the contact anyway. For contacts who gave their details themselves, Art. 13 applies at the time of collection (business card, event registration, inbound request). Status: **[recommendation]** (S8 and a privacy notice page).

**Art. 21.** Objection to direct marketing is absolute (Art. 21(2)–(3)): stop at once and keep the address on a suppression list. It must be presented clearly and separately, at the latest at the first communication (Art. 21(4)). Status: **[recommendation]** (S7, S8).

**Profiling.** The intent score is about the company account, computed from interactions with its contacts. It is not used to decide anything about a contact, and contacts are not ranked. If it were ever computed per contact, it would be profiling for direct marketing, and the objection right would apply to it explicitly.

## 7. E-mail marketing rules (B2B)

These rules sit outside the GDPR (ePrivacy Directive Art. 13 as implemented nationally). What follows is the general position; **every point needs confirmation by counsel for the countries actually written to.**

- **Sweden** (sender's country, and the rule for Swedish recipients). The Marketing Act (2008:486) requires prior consent for e-mail marketing to **natural persons** (§19), with an exception for existing customers who bought similar products and were offered an opt-out. E-mail to a **legal person** is allowed without prior consent unless the recipient has clearly objected, and every marketing e-mail must give a valid address where the recipient can ask, free of charge, for the marketing to stop (§20). Sole traders count as natural persons. Whether an e-mail to a named employee's work address counts as marketing to the company or to the person is a recurring question. **[counsel]**
- **Recipients in other member states.** ABC Tech's prospects are in Germany, the Netherlands, France, Poland, Austria, Italy and Spain, and the rules of the recipient's country generally apply. They differ. Germany treats advertising e-mail without the recipient's prior express consent as an unreasonable nuisance for businesses too (UWG §7(2) no. 2, with an existing-customer exception in §7(3)), so cold B2B e-mail to German contacts is not an option. **[counsel]** for each country.
- **Follow-ups.** The follow-up branch writes only to existing CRM contacts of an account the salesperson can read, and its fallback template assumes an earlier conversation ("thank you for your time with ABC Tech"). The code does not check that a conversation took place. Where one did, the expectation argument is stronger. Whether such a message is still "advertising" under national law depends on its content; courts read the term broadly. **[counsel]**

Practical rule until counsel confirms: use `/followup` only for contacts with an existing business conversation; no cold outreach by e-mail through the platform; every e-mail carries the S8 footer.

## 8. Review

Review this assessment when the web sources change (S9), when person-level scoring is considered, when the e-mail channel becomes real, or in 12 months.
