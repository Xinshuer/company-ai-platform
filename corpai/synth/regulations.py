"""The local regulation library (legal.regulations + corpus collection legal_regs).

Short paraphrased summaries written for this demo, not the official text. Article numbers refer
to Regulation (EU) 2016/679 (GDPR) and Regulation (EU) 2024/1689 (AI Act). source_uri points at the
company's internal mirror, which (like every domain here) ends in .example.
"""

from __future__ import annotations

_MIRROR = "https://regs.kestrelbyte.example"

_GDPR: list[tuple[int, str, str]] = [
    (3, "Territorial scope",
     "Applies to processing in the context of an EU establishment, wherever the processing happens, and to "
     "controllers or processors outside the EU that offer goods or services to people in the EU or monitor their "
     "behaviour there."),
    (5, "Principles relating to processing of personal data",
     "Personal data must be processed lawfully, fairly and transparently; collected for specified purposes; limited "
     "to what is necessary; accurate; kept no longer than needed; and protected by appropriate security. The "
     "controller must be able to demonstrate compliance (accountability)."),
    (6, "Lawfulness of processing",
     "Processing is lawful only on one of six bases: consent, performance of a contract, a legal obligation, vital "
     "interests, a task in the public interest, or the legitimate interests of the controller or a third party "
     "unless overridden by the data subject's interests and rights."),
    (7, "Conditions for consent",
     "The controller must be able to show that consent was given. A request for consent must be clearly "
     "distinguishable, consent can be withdrawn at any time as easily as it was given, and it must be freely given."),
    (9, "Processing of special categories of personal data",
     "Data revealing racial or ethnic origin, political opinions, religious beliefs, trade union membership, genetic "
     "data, biometric data used for identification, health data and data about sex life or sexual orientation may "
     "not be processed unless a listed exception applies, such as explicit consent or obligations in employment law."),
    (12, "Transparent information, communication and modalities",
     "Information to data subjects must be concise, transparent, intelligible and in plain language. Requests must "
     "be answered without undue delay and within one month, extendable by two further months for complex requests."),
    (13, "Information to be provided where data are collected from the data subject",
     "At collection the controller tells the person who it is, how to contact the DPO, the purposes and legal basis, "
     "recipients, any transfers outside the EU, the retention period and the person's rights."),
    (14, "Information to be provided where data have not been obtained from the data subject",
     "When data comes from another source the controller gives the same information plus the categories of data and "
     "the source, within a reasonable period and at the latest within one month."),
    (15, "Right of access by the data subject",
     "A person may obtain confirmation whether their data is processed, a copy of it, and information about the "
     "purposes, recipients, retention, source and any automated decision-making."),
    (17, "Right to erasure",
     "A person can have their data erased without undue delay when, for example, it is no longer needed, consent is "
     "withdrawn, they object successfully, or the processing was unlawful, subject to exceptions such as legal "
     "obligations or legal claims."),
    (21, "Right to object",
     "A person can object to processing based on legitimate or public interests; the controller must stop unless it "
     "shows compelling legitimate grounds. Objection to direct marketing must always be honoured."),
    (22, "Automated individual decision-making, including profiling",
     "A person has the right not to be subject to a decision based solely on automated processing that produces "
     "legal or similarly significant effects. Exceptions (contract, law, explicit consent) require safeguards, at "
     "least the right to human intervention, to express a view and to contest the decision."),
    (24, "Responsibility of the controller",
     "The controller implements appropriate technical and organisational measures, proportionate to the risks, and "
     "must be able to demonstrate that processing complies with the regulation."),
    (25, "Data protection by design and by default",
     "Data protection must be built into processing from the design stage, and by default only the personal data "
     "necessary for each specific purpose is processed and made accessible."),
    (28, "Processor",
     "Controllers may only use processors that give sufficient guarantees. Processing is governed by a contract that "
     "covers documented instructions, confidentiality, security, conditions for sub-processors, assistance with data "
     "subject rights and breaches, deletion or return of data at the end, and audits."),
    (30, "Records of processing activities",
     "Controllers and processors keep written records of processing: purposes, categories of data and data subjects, "
     "recipients, transfers, retention periods and a description of security measures."),
    (32, "Security of processing",
     "Appropriate security measures based on risk, such as pseudonymisation and encryption, ongoing confidentiality, "
     "integrity, availability and resilience, the ability to restore access after an incident, and regular testing."),
    (33, "Notification of a personal data breach to the supervisory authority",
     "The controller notifies the supervisory authority without undue delay and where feasible within 72 hours of "
     "becoming aware of a breach, unless it is unlikely to result in a risk. Processors notify the controller "
     "without undue delay."),
    (34, "Communication of a personal data breach to the data subject",
     "When a breach is likely to result in a high risk to people's rights and freedoms, the controller informs the "
     "affected people without undue delay in clear language."),
    (35, "Data protection impact assessment",
     "Before processing likely to result in a high risk, notably systematic and extensive evaluation including "
     "profiling with significant effects, large-scale special-category data or systematic monitoring of public "
     "areas, the controller carries out a DPIA."),
    (37, "Designation of the data protection officer",
     "A DPO is required for public authorities and where core activities involve large-scale regular and systematic "
     "monitoring or large-scale processing of special categories. National law may add further cases."),
    (44, "General principle for transfers",
     "Personal data may be transferred to a third country or international organisation only if the conditions of "
     "Chapter V are met, so that the level of protection is not undermined."),
    (45, "Transfers on the basis of an adequacy decision",
     "Transfers may take place without specific authorisation to a country the European Commission has found to "
     "ensure an adequate level of protection."),
    (46, "Transfers subject to appropriate safeguards",
     "Without an adequacy decision, transfers need appropriate safeguards such as standard contractual clauses or "
     "binding corporate rules, with enforceable rights and effective remedies for data subjects."),
    (49, "Derogations for specific situations",
     "Without adequacy or safeguards, a transfer may rely on narrow derogations, for example explicit informed "
     "consent or necessity for a contract with the data subject; they are meant for occasional transfers."),
]

_AI_ACT: list[tuple[str, str, str, str]] = [
    ("AI-Act-Art-4", "AI literacy", "in_force",
     "Providers and deployers of AI systems take measures to ensure a sufficient level of AI literacy among staff "
     "who operate or use AI systems. Applies since 2 February 2025."),
    ("AI-Act-Art-5", "Prohibited AI practices", "in_force",
     "Bans certain practices, including manipulative techniques, social scoring and emotion recognition in the "
     "workplace except for medical or safety reasons. Applies since 2 February 2025."),
    ("AI-Act-Annex-III-4", "High-risk AI: employment and workers management", "adopted_not_applicable",
     "AI systems used to recruit or select people, in particular to filter applications and evaluate candidates, "
     "are high-risk under Article 6(2) and Annex III point 4, with obligations on risk management, data governance, "
     "human oversight and logging. Adopted; the obligations for Annex III systems were scheduled for 2 August 2026 "
     "and are being postponed under the Digital Omnibus on AI, so legal re-checks the application date each quarter."),
]


def _rows() -> list[dict]:
    rows = [{"reg_id": f"GDPR-Art-{n}", "title": f"GDPR Article {n}: {title}", "status": "in_force",
             "summary": summary, "source_uri": f"{_MIRROR}/gdpr/art-{n}"} for n, title, summary in _GDPR]
    rows += [{"reg_id": rid, "title": f"EU AI Act: {title}", "status": status, "summary": summary,
              "source_uri": f"{_MIRROR}/ai-act/{rid.lower()}"} for rid, title, status, summary in _AI_ACT]
    return rows


REGULATIONS: list[dict] = _rows()
