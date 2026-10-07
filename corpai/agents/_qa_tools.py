"""Function-calling definitions of the read tools the Q&A agent may be offered (config/agents.yaml).

The model sees `<domain>__<tool>` names (dots are not allowed in OpenAI tool names). What it may
call is decided by code: the agent's allowlist ∩ policy.tool_allowed for this user ∩ read tier.
A name the model invents, or one it was not offered, is refused before any MCP call.
"""

from __future__ import annotations

from typing import Any

_S = {"type": "string"}
_I = {"type": "integer"}

# (description, properties, required)
SCHEMAS: dict[str, tuple[str, dict[str, Any], list[str]]] = {
    "docs.search_docs": ("Search documents you may read.", {"query": _S, "k": _I}, ["query"]),
    "docs.get_doc": ("Read one document by id.", {"doc_id": _S}, ["doc_id"]),
    "crm.search_accounts": ("CRM accounts you may see; query matches name, domain, industry or city "
                            "(empty = all you may see).", {"query": _S, "limit": _I}, []),
    "crm.get_account": ("One CRM account with contacts and open opportunities.", {"account_id": _S},
                        ["account_id"]),
    "code.get_project_status": ("Stage-level status of a project, or of every project you may see.",
                                {"project_id": _S}, []),
    "code.search_tickets": ("Engineering tickets you may see.", {"query": _S, "project_id": _S, "status": _S,
                                                                 "limit": _I}, []),
    "finance.list_metrics": ("Published company metrics.", {}, []),
    "finance.get_metric": ("One published metric.", {"metric_id": _S, "period": _S}, ["metric_id"]),
    "hris.headcount": ("Headcount by department (small groups suppressed).", {}, []),
    "hris.list_requisitions": ("Open roles and their headcount.", {"status": _S}, []),
    "legal.search_regulations": ("GDPR / AI Act library: public summaries with article numbers.",
                                 {"query": _S, "limit": _I}, []),
    "legal.contracts_expiring": ("Signed contracts expiring within N days or in a month (YYYY-MM).",
                                 {"within_days": _I, "month": _S}, []),
}


def tool_name(qualified: str) -> str:
    return qualified.replace(".", "__", 1)


def definitions(qualified_tools: list[str]) -> list[dict]:
    """OpenAI `tools` entries for the offered tools (only those with a known schema)."""
    out = []
    for q in qualified_tools:
        if q not in SCHEMAS:
            continue
        desc, props, required = SCHEMAS[q]
        out.append({"type": "function", "function": {
            "name": tool_name(q), "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required,
                           "additionalProperties": False}}})
    return out
