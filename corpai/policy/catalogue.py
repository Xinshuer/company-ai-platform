"""Fixed vocabularies the rules refer to: resource kinds, the MCP tool catalogue with tiers
(architecture §11), domain → owning department, RAG collections (§10)."""

from __future__ import annotations

from corpai.types import Dept, ToolTier

# Kinds whose rows carry a Label; reads on them are decided by the label exactly like RLS (§5.1).
# Extra kinds beyond the §6 catalogue are the other labelled tables of §5.1, so callers that pass
# them get the label rule instead of a default deny.
LABELLED_KINDS: frozenset[str] = frozenset({
    "doc", "rag.chunk", "playbook",
    "crm.account", "crm.contact", "crm.opportunity", "crm.interaction", "crm.quote",
    "eng.project", "eng.ticket", "eng.ci_run", "eng.vuln", "eng.repo_doc",
    "legal.contract", "legal.contract_request", "legal.matter", "legal.regulation", "legal.clause",
    "fin.ledger", "fin.invoice", "fin.budget", "fin.statement", "fin.metric",
    "hr.employee", "hr.compensation", "hr.performance", "hr.requisition", "hr.requisition_budget",
    "hr.candidate", "hr.screening",
    "web.page",
})

# Kinds without a meaningful row label; role rules decide them.
UNLABELLED_KINDS: frozenset[str] = frozenset({
    "audit", "trace", "proposal", "report", "hr.aggregate", "admin.outage_switch", "admin.budget",
})

TOOL_CATALOGUE: dict[str, dict[str, ToolTier]] = {
    "crm": {
        "search_accounts": ToolTier.READ, "get_account": ToolTier.READ,
        "list_opportunities": ToolTier.READ, "get_interactions": ToolTier.READ,
        "log_interaction": ToolTier.INTERNAL, "update_opportunity_stage": ToolTier.INTERNAL,
        "send_email": ToolTier.EXTERNAL,
    },
    "code": {
        "search_tickets": ToolTier.READ, "get_ticket": ToolTier.READ, "list_ci_runs": ToolTier.READ,
        "get_project_status": ToolTier.READ, "search_code": ToolTier.READ,
        "create_ticket": ToolTier.INTERNAL, "comment_ticket": ToolTier.INTERNAL,
    },
    "docs": {"search_docs": ToolTier.READ, "get_doc": ToolTier.READ},
    "finance": {
        "list_metrics": ToolTier.READ, "get_metric": ToolTier.READ, "query_ledger": ToolTier.READ,
        "list_invoices": ToolTier.READ, "reconcile": ToolTier.READ,
        "post_journal_entry": ToolTier.EXTERNAL,
    },
    "hris": {
        "get_employee": ToolTier.READ, "headcount": ToolTier.READ, "list_requisitions": ToolTier.READ,
        "list_candidates": ToolTier.READ, "get_candidate": ToolTier.READ,
        "create_requisition": ToolTier.INTERNAL,
    },
    "legal": {
        "search_contracts": ToolTier.READ, "get_contract": ToolTier.READ,
        "contracts_expiring": ToolTier.READ, "get_clause_library": ToolTier.READ,
        "search_regulations": ToolTier.READ,
        "create_contract_draft": ToolTier.INTERNAL, "send_contract": ToolTier.EXTERNAL,
    },
    "web": {"search": ToolTier.READ, "fetch": ToolTier.READ},
}

# Whose head approves a domain's write proposals (design-review 五, can_approve).
DOMAIN_DEPT: dict[str, Dept] = {
    "crm": Dept.SALES, "code": Dept.ENG, "finance": Dept.FINANCE, "hris": Dept.HR, "legal": Dept.LEGAL,
}

COLLECTIONS: frozenset[str] = frozenset({
    "public", "general", "eng", "sales", "legal", "legal_regs", "finance", "hr", "exec", "playbooks",
})

# A user's own department collection for the qa purpose. IT has none.
DEPT_COLLECTION: dict[Dept, str] = {
    Dept.SALES: "sales", Dept.ENG: "eng", Dept.LEGAL: "legal", Dept.FINANCE: "finance",
    Dept.HR: "hr", Dept.EXEC: "exec",
}


def tool_tier(domain: str, tool: str) -> ToolTier | None:
    return TOOL_CATALOGUE.get(domain, {}).get(tool)
