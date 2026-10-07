"""The §5.1 ACL contract (architecture.md): which level and acl_groups each kind of row gets.

Synth writes exactly these, policy and RLS read them, and tests/synth/test_acl_contract.py
recomputes them row by row. Keep this module the single place the mapping is spelled out.
"""

from __future__ import annotations

PUBLIC = (1, ["all"])  # site pages, press releases, published JDs, GDPR library
GENERAL = (2, ["all"])  # handbook, policies, templates


def dept_internal(dept: str) -> list[str]:
    """Dept-internal docs (wiki, runbooks, playbooks): L2/L3 by content."""
    return [f"dept:{dept}", f"head:{dept}", "role:ceo"]


def crm(owner_id: str, account_id: str) -> list[str]:
    # grant:crm:<account> lets a lawyer see one account for a contract-review ticket (core.grants)
    return [f"user:{owner_id}", "head:sales", "role:ceo", f"grant:crm:{account_id}"]


def eng_project(project_id: str) -> list[str]:
    # stage-level status is visible to the whole of eng and to sales (design-review §5)
    return [f"proj:{project_id}", "dept:eng", "head:eng", "dept:sales", "role:ceo"]


def eng_work(project_id: str) -> list[str]:
    """Tickets, CI runs, repo docs."""
    return [f"proj:{project_id}", "head:eng"]


VULN = (4, ["head:eng", "role:ceo"])


def contract(handler_id: str) -> list[str]:
    return [f"user:{handler_id}", "head:legal", "role:ceo"]


def contract_request(requester_id: str) -> list[str]:
    return [f"user:{requester_id}", "head:sales", "dept:legal", "head:legal"]


def matter(matter_id: str) -> list[str]:
    # The CEO is deliberately absent: litigation files need an explicit grant (privilege), which is
    # a core.grants row with grp grant:matter:<id>, the same convention as grant:crm:<account>.
    return [f"matter:{matter_id}", "head:legal", f"grant:matter:{matter_id}"]


FIN = (3, ["dept:finance", "head:finance", "role:ceo"])  # ledger, invoices, budgets
FIN_STATEMENT = (4, ["head:finance", "dept:finance", "role:ceo"])  # statements, funding


def hr_employee(employee_id: str) -> list[str]:
    return ["dept:hr", "head:hr", f"mgr-of:{employee_id}"]


HR_RESTRICTED = (4, ["head:hr"])  # compensation, performance


def requisition(req_id: str, hiring_dept: str) -> list[str]:
    return ["dept:hr", "head:hr", f"head:{hiring_dept}", "role:ceo", f"req:{req_id}"]


def requisition_budget(hiring_dept: str) -> list[str]:
    return ["head:hr", f"head:{hiring_dept}", "head:finance", "role:ceo"]


def candidate(req_id: str) -> list[str]:
    """Candidates and résumés (L3); screenings use the same groups at L4."""
    return [f"req:{req_id}", "head:hr"]


EXEC_BOARD = (4, ["role:ceo", "dept:exec"])

# §5.1: canary rows/docs are L4 whatever the host row's ACL, so no L3 reader of the host (the
# owning rep, project members) gets a canary through RLS, and the stored label agrees with what
# classify_text says about the content (a canary always classifies L4).
CANARY_LEVEL = 4
