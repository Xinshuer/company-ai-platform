"""The decision tables (architecture §6, design-review 五). Default deny; deny rules override.

Read rules on labelled rows mirror `core.can_read(level, acl_groups)` exactly (§5.1), so decide()
and RLS agree by construction. Everything else is a role rule on an unlabelled kind.
"""

from __future__ import annotations

from corpai.policy.approvals import can_approve
from corpai.policy.catalogue import DEPT_COLLECTION, LABELLED_KINDS, UNLABELLED_KINDS
from corpai.policy.model import Derived, Effect, ProposalView, Rule, ToolRule
from corpai.types import Action, Dept, Level, Principal, Resource, Role, Trust

ALL_KINDS = LABELLED_KINDS | UNLABELLED_KINDS
ALL_ACTIONS = frozenset(Action)
READS = frozenset({Action.READ, Action.SEARCH})
OPS_ROLES = frozenset({Role.PLATFORM_ADMIN, Role.SECURITY_AUDITOR})


def _f(*xs) -> frozenset:
    return frozenset(xs)


# ---- predicates ---------------------------------------------------------------------------

def label_readable(p: Principal, r: Resource) -> bool:
    """Same test as core.can_read(): clearance ceiling AND at least one shared ACL group."""
    return int(r.label.level) <= int(p.clearance) and bool(r.label.acl_groups & p.groups)


def _inactive(p: Principal, r: Resource) -> bool:
    return not p.active


def _ops_role_on_confidential(p: Principal, r: Resource) -> bool:
    # Defence in depth: their clearance is L2 already, but a mislabelled ACL must not open L3+.
    return p.role in OPS_ROLES and int(r.label.level) >= Level.L3


def _trace_owner(p: Principal, r: Resource) -> bool:
    owner = r.attrs.get("owner_id")
    return bool(owner) and owner == p.user_id


def _trace_dept(r: Resource) -> str | None:
    d = r.attrs.get("dept") or r.label.dept
    return str(d) if d else None


def _trace_head(p: Principal, r: Resource) -> bool:
    d = _trace_dept(r)
    return d is not None and f"head:{d}" in p.groups


def _report_head_own_domain(p: Principal, r: Resource) -> bool:
    return p.role is Role.MANAGER and r.attrs.get("domain") == str(p.dept)


def _hr_head(p: Principal, r: Resource) -> bool:
    return p.role is Role.MANAGER and p.dept is Dept.HR and "head:hr" in p.groups


# HR aggregates a non-HR head may see for their own department: people counts only. Anything else
# (pay, performance, ...) or a missing topic is not covered (default deny).
HEAD_OWN_DEPT_HR_TOPICS = frozenset({"headcount"})


def _head_own_dept_headcount(p: Principal, r: Resource) -> bool:
    return (p.role is Role.MANAGER and f"head:{p.dept}" in p.groups
            and r.attrs.get("dept") == str(p.dept) and r.attrs.get("topic") in HEAD_OWN_DEPT_HR_TOPICS)


def _proposal_requester(p: Principal, r: Resource) -> bool:
    req = r.attrs.get("requester_id")
    return bool(req) and req == p.user_id


def _proposal_approver(p: Principal, r: Resource) -> bool:
    view = ProposalView.from_attrs(r.attrs)
    return view is not None and can_approve(p, view).allow


# ---- decide(): deny rules, then allow rules (first match), then default deny ----------------

DENY_RULES: list[Rule] = [
    Rule("deny-inactive", ALL_KINDS, ALL_ACTIONS, _inactive, Effect.DENY,
         "the account is deactivated"),
    Rule("deny-ops-roles-confidential", LABELLED_KINDS,
         _f(Action.READ, Action.SEARCH, Action.EXPORT, Action.PROPOSE),
         _ops_role_on_confidential, Effect.DENY,
         "platform admins and security auditors run the system and read no L3+ business content"),
]

ALLOW_RULES: list[Rule] = [
    Rule("label-acl", LABELLED_KINDS, READS, label_readable, Effect.ALLOW,
         "label level within clearance and a shared ACL group"),
    Rule("propose-on-readable", LABELLED_KINDS, _f(Action.PROPOSE), label_readable, Effect.ALLOW,
         "you may propose changes to rows you can read; tool rules and approval still apply"),
    Rule("audit-auditor", _f("audit"), READS, _f(Role.SECURITY_AUDITOR), Effect.ALLOW,
         "only the security auditor reads the audit log"),
    Rule("trace-owner", _f("trace"), _f(Action.READ), _trace_owner, Effect.ALLOW,
         "you started this run"),
    Rule("trace-head-aggregate", _f("trace"), _f(Action.READ), _trace_head, Effect.ALLOW,
         "department heads see aggregate trace metrics of their department only",
         obligations=_f("aggregate_only")),
    Rule("trace-platform-admin", _f("trace"), _f(Action.READ), _f(Role.PLATFORM_ADMIN), Effect.ALLOW,
         "platform admins see trace metadata only; content needs break-glass",
         obligations=_f("metadata_only")),
    Rule("admin-platform", _f("admin.outage_switch", "admin.budget"), _f(Action.READ, Action.ADMIN),
         _f(Role.PLATFORM_ADMIN), Effect.ALLOW, "platform admins operate switches and budgets"),
    Rule("report-ceo", _f("report"), _f(Action.READ), _f(Role.CEO), Effect.ALLOW,
         "the CEO reads management reports of every domain"),
    Rule("report-head-own-domain", _f("report"), _f(Action.READ), _report_head_own_domain, Effect.ALLOW,
         "department heads read reports of their own domain"),
    # design-review 五, 薪酬、员工档案: company-wide department-level aggregates (pay, performance,
    # headcount) go to the CEO and the head of HR only; other heads see people counts of their own
    # department, never pay or performance of another.
    Rule("hr-aggregate-ceo", _f("hr.aggregate"), _f(Action.READ), _f(Role.CEO), Effect.ALLOW,
         "the CEO sees department-level HR aggregates; groups under 5 people are hidden",
         obligations=_f("aggregate_only", "min_group_5")),
    Rule("hr-aggregate-hr-head", _f("hr.aggregate"), _f(Action.READ), _hr_head, Effect.ALLOW,
         "the head of HR sees department-level HR aggregates; groups under 5 people are hidden",
         obligations=_f("aggregate_only", "min_group_5")),
    Rule("hr-aggregate-head-own-headcount", _f("hr.aggregate"), _f(Action.READ), _head_own_dept_headcount,
         Effect.ALLOW, "department heads see the headcount of their own department",
         obligations=_f("aggregate_only", "min_group_5")),
    Rule("proposal-requester", _f("proposal"), _f(Action.READ), _proposal_requester, Effect.ALLOW,
         "you requested this proposal"),
    Rule("proposal-approver", _f("proposal"), _f(Action.READ, Action.APPROVE), _proposal_approver,
         Effect.ALLOW, "you are an eligible approver for this proposal"),
]

# EXPORT is allowed exactly where READ is, and the export must carry a watermark.
DERIVED: list[Derived] = [
    Derived(Action.EXPORT, Action.READ, "export-as-read", obligations=_f("watermark")),
]


# ---- tool_allowed(): user rules per domain (design-review 五) --------------------------------

def _human(p: Principal) -> bool:
    return p.active and p.role is not Role.SERVICE


def _in_dept(*depts: Dept):
    return lambda p, agent: _human(p) and p.dept in depts and p.role not in OPS_ROLES


def _ceo(p: Principal, agent: str) -> bool:
    return _human(p) and p.role is Role.CEO


def _head(p: Principal, agent: str) -> bool:
    return _human(p) and p.role is Role.MANAGER


def _human_not_ops(p: Principal, agent: str) -> bool:
    # Platform admin and auditor run the system; design-review 五 gives them no business data.
    return _human(p) and p.role not in OPS_ROLES


def _either(*preds):
    return lambda p, agent: any(f(p, agent) for f in preds)


def _legal_with_crm_grant(p: Principal, agent: str) -> bool:
    # Legal looks at one CRM account by ticket: the grant group is what makes it time-boxed.
    return _in_dept(Dept.LEGAL)(p, agent) and any(g.startswith("grant:crm:") for g in p.groups)


def _sales_web(p: Principal, agent: str) -> bool:
    # Web content is untrusted: only the isolated sub-agent with no other tools may touch it.
    return agent == "sales_web" and _either(_in_dept(Dept.SALES), _ceo)(p, agent)


TOOL_RULES: list[ToolRule] = [
    ToolRule("tool-crm-sales-ceo", "crm", None, _either(_in_dept(Dept.SALES), _ceo),
             "CRM belongs to sales; the CEO may use it"),
    ToolRule("tool-crm-legal-grant", "crm", _f("get_account"), _legal_with_crm_grant,
             "legal may open one CRM account with a time-boxed grant"),
    ToolRule("tool-code-eng-ceo", "code", None, _either(_in_dept(Dept.ENG), _ceo),
             "code tools belong to engineering; the CEO may use them"),
    ToolRule("tool-code-sales-status", "code", _f("get_project_status"), _in_dept(Dept.SALES),
             "sales may see stage-level project status"),
    ToolRule("tool-docs-humans", "docs", None, lambda p, agent: _human(p),
             "every active employee may search documents; RLS filters rows"),
    ToolRule("tool-finance-dept-ceo", "finance", None, _either(_in_dept(Dept.FINANCE), _ceo),
             "finance tools belong to finance; the CEO may use them"),
    ToolRule("tool-finance-published-metrics", "finance", _f("list_metrics", "get_metric"), _human_not_ops,
             "every employee may read published metrics (design-review 五: 其他部门只看已发布的指标); "
             "decide() on each metric's label limits the rows: drafts carry the finance ACL"),
    ToolRule("tool-hris-hr", "hris", None, _in_dept(Dept.HR),
             "HRIS belongs to HR"),
    ToolRule("tool-hris-heads", "hris", _f("headcount", "list_requisitions", "get_employee"), _head,
             "heads see headcount, requisitions and their direct reports (RLS limits rows)"),
    ToolRule("tool-hris-ceo", "hris", _f("headcount", "list_requisitions"), _ceo,
             "the CEO sees headcount and requisitions"),
    ToolRule("tool-legal-dept-ceo", "legal", None, _either(_in_dept(Dept.LEGAL), _ceo),
             "legal tools belong to legal; the CEO may use them"),
    ToolRule("tool-legal-regulations-all", "legal", _f("search_regulations"), _human_not_ops,
             "the GDPR / AI Act library is public (L1, acl all): every employee may search it"),
    ToolRule("tool-web-sales-subagent", "web", None, _sales_web,
             "web search is only for sales and the CEO, through the isolated sales_web agent"),
]


# ---- rag_filter(): collections per purpose ---------------------------------------------------

_REPORT_COLLECTIONS = _f("public", "general", "sales", "eng", "legal", "finance", "hr", "exec")


def _qa_collections(p: Principal) -> frozenset[str]:
    # legal_regs is the public regulation library (L1, acl all): design-review 五 lets every
    # department read it, so it is in everyone's qa scope, not only legal's.
    cols = {"public", "general", "legal_regs"}
    own = DEPT_COLLECTION.get(p.dept)
    if own:
        cols.add(own)
    return frozenset(cols)


# purpose -> (collections for p, minimum trust)
PURPOSES = {
    "qa": (_qa_collections, None),
    "report": (lambda p: _REPORT_COLLECTIONS, Trust.REVIEWED),
    "playbook": (lambda p: _f("playbooks"), Trust.REVIEWED),
    "legal_review": (lambda p: _f("legal", "legal_regs", "playbooks"), None),
    "sales_research": (lambda p: _f("public", "sales"), None),
    "hr_screening": (lambda p: _f("hr"), None),
}

# Platform admin and auditor never search business collections, whatever the purpose.
OPS_ROLE_COLLECTIONS = _f("public", "general")
