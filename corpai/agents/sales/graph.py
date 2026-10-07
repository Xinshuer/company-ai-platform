"""sales_profile graph (M4; design-review 二.6, 三 销售; decision D5).

Two branches, chosen by code from the input:

Profile (input: a company name)
    intake    the input must be a plain company name at L1 (it becomes the web query)
    web       the isolated sales_web sub-agent (corpai/agents/sales/web.py): public pages ->
              CompanyProfile (fixed fields); only the profile and metadata come back
    score     CRM as the user (RLS): the account by name, intent_score (code, no model), contacts;
              fit_score(profile), tier, select_playbook (L3)
    strategy  local model writes prose from the profile's enum fields, the scores and the playbook

Follow-up e-mail (input "/followup [CT-xxxxx]" in a thread that holds a profile)
    draft_email     fresh CRM rows + the playbook entry of the stored tier -> local model drafts JSON
    propose_email   crm.send_email: a tier-3 proposal (the tool files it, nothing is sent)
    wait            approval gate: the head of sales (or the CEO) decides, four eyes
    after           reports what happened

Rule of Two: the profile branch holds untrusted input (web) and sensitive data (CRM) but no
external action; the follow-up branch holds sensitive data and external action but no untrusted
input. The follow-up reads only what code computed in the earlier run (account id, tier) plus fresh
CRM rows and the reviewed playbook, never the profile or anything from a page; the user's own
"draft follow-up" request starts it, and the proposal still needs another person's approval.
The CompanyProfile's free-text-like fields (company, domain, sources) never reach a model here: the
strategy prompt uses the CRM account name and the profile's enum and number fields only.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, START, StateGraph

from corpai.agents.runtime import BaseState, RunContext, Step, approval_gate, node
from corpai.agents.sales import _email, _fake
from corpai.agents.sales.web import run_web_research
from corpai.classify import classify_text
from corpai.sales import CompanyProfile, NoPermission, fit_score, intent_score, select_playbook, tier
from corpai.types import Level

AGENT = "sales_profile"
APP = "sales_profile"
FOLLOWUP_RE = re.compile(r"^/followup(?:\s+(CT-\d{5}))?\s*$", re.IGNORECASE)
# Mirrors web.search's own check (corpai/mcp_servers/web.py); the server checks again.
_COMPANY = re.compile(r"^[^\W\d_][\w .,&'()/+-]{1,99}$", re.UNICODE)
_MAX_WORDS, _MAX_DIGITS = 8, 4

# Fields of CompanyProfile a model may read: enums and numbers only. company / domain / sources
# are page strings an injected page could shape (within the schema), so they stay out of prompts.
PROMPT_FIELDS = ("industry", "hq_country", "countries", "fleet_size_band", "employees_band", "funding_stage",
                 "last_funding_eur", "growth_signals", "tech_signals")

STRATEGY_SYSTEM = (
    "You are a sales coach at ABC Tech (FleetPilot route planning). Write a short follow-up strategy "
    "for the account in the JSON: three to five sentences of plain prose. Use only the facts given. "
    "Promise only what playbook.allowed_commitments lists; name what needs approval; never mention "
    "anything in playbook.forbidden_commitments as an offer. Do not invent figures, names or dates.")
FOLLOWUP_SYSTEM = (
    "You draft a short, polite follow-up e-mail from an ABC Tech account executive to the contact "
    "in the JSON. Suggest the first of playbook.next_steps. Offer nothing outside "
    "playbook.allowed_commitments. No links, no e-mail addresses, no attachments, no prices. "
    "Answer ONLY with a JSON object {\"subject\": string, \"body\": string}.")

NOTICES = {
    "not_a_company": {"zh": "网页调研只接受公司名称（例如 Havenmeer Distributie B.V.），不接受其他内容。",
                      "en": "Web research takes a company name only (for example Havenmeer Distributie B.V.)."},
    "followup_needs_profile": {"zh": "请先在这个会话里为一家公司生成画像，再起草跟进邮件。",
                               "en": "Profile a company in this conversation first, then draft the follow-up."},
    "intent_denied": {"zh": "没有权限读取这家公司的 CRM 互动（或 CRM 里没有它），所以没有意向分和跟进策略。",
                      "en": "No permission for this company's CRM activity (or it is not in the CRM): no intent "
                            "score and no follow-up strategy."},
    "web_failed": {"zh": "没有找到可用的公开网页，所以没有匹配度。",
                   "en": "No usable public pages were found, so there is no fit score."},
}


class SalesState(BaseState, total=False):
    question: str
    mode: str | None  # profile | followup | refused
    company: str | None
    contact_choice: str | None
    profile: dict | None
    web: dict | None
    account: dict | None
    contacts: list[dict]
    fit: dict | None
    intent: dict | None
    tier: str | None
    playbook: dict | None
    steps: list[dict]
    draft: dict | None
    proposal_id: str | None
    decision: dict | None
    output: dict | None


def initial_state(text: str) -> dict:
    """Per-run keys. account and tier stay in the thread for a later follow-up e-mail."""
    return {"question": text, "mode": None, "output": None, "draft": None, "proposal_id": None, "decision": None,
            "contact_choice": None}


def _notice(code: str) -> dict:
    return {"t": "notice", "code": code, **NOTICES[code]}


def _plain_company(text: str) -> bool:
    return bool(_COMPANY.match(text) and "@" not in text and "://" not in text
                and len(text.split()) <= _MAX_WORDS and sum(ch.isdigit() for ch in text) <= _MAX_DIGITS)


def _out(text: str, data: dict) -> dict:
    return {"text": text, "citations": [], "data": data}


_RESET = {"company": None, "profile": None, "web": None, "account": None, "contacts": [], "fit": None,
          "intent": None, "tier": None, "playbook": None, "steps": []}


@node(caps={"sensitive_data"})
async def intake(state: SalesState, runtime: Any) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    text = (state.get("question") or "").strip()
    user_level = Level(max(1, min(4, int(ctx.user_level))))
    c = classify_text(text)
    step.event({"t": "classify", "level": int(c.level), "findings": sorted({f.kind for f in c.findings})})
    step.raise_to(max(c.level, user_level), "input" if c.level >= user_level else "user_level")
    m = FOLLOWUP_RE.match(text)
    if m:
        if not state.get("account") or not state.get("tier"):
            step.event(_notice("followup_needs_profile"))
            return step.update(mode="refused", output=_out(NOTICES["followup_needs_profile"]["en"],
                                                           {"kind": "sales_followup", "refused": "no_profile"}))
        return step.update(mode="followup", contact_choice=m.group(1))
    # The company name becomes the web query: it must be a plain name and public (L1), never a
    # sentence, a person or an internal figure (the search server refuses those as well).
    if c.level > Level.L1 or not _plain_company(text):
        step.event(_notice("not_a_company"))
        return step.update(mode="refused", **_RESET,
                           output=_out(NOTICES["not_a_company"]["en"], {"kind": "sales_profile",
                                                                        "refused": "not_a_company"}))
    return step.update(mode="profile", **{**_RESET, "company": text})


@node(caps={"untrusted_input"})
async def web(state: SalesState, runtime: Any) -> dict:
    step = Step(runtime, state)
    res = await run_web_research(runtime.context, state.get("company") or "")
    for e in res["events"]:
        step.event(e)
    meta = res["meta"]
    steps = [{"step": "web_extract", "route": meta["route"], "level": meta["level"]}] if meta else []
    # Only the validated profile (L1, fixed fields) and text-free metadata cross into this graph.
    return step.update(profile=res["profile"], web={**(meta or {}), "error": res["error"]}, steps=steps)


def _pick_account(rows: list[dict], company: str) -> dict | None:
    exact = [a for a in rows if str(a.get("name", "")).casefold() == company.casefold()]
    pick = exact[0] if len(exact) == 1 else (rows[0] if len(rows) == 1 else None)
    if pick is None or not isinstance(pick.get("account_id"), str):
        return None
    return {"account_id": pick["account_id"], "name": str(pick.get("name") or ""), "status": pick.get("status")}


@node(caps={"sensitive_data"})
async def score(state: SalesState, runtime: Any) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    company = state.get("company") or ""
    env = await step.call_tool("crm", "search_accounts", {"query": company, "limit": 5})
    rows = (env.data or {}).get("accounts") if env.ok and isinstance(env.data, dict) else None
    account = _pick_account([r for r in rows or [] if isinstance(r, dict)], company)

    intent: dict = {"status": "no_permission"}
    if account is not None:
        try:
            value, reasons = await intent_score(ctx.principal, account["account_id"], run_id=ctx.run_id)
            intent = {"status": "ok", "score": value, "reasons": reasons}
            step.raise_to(Level.L3, "crm:intent")  # interactions, opportunities, quotes: L3
        except NoPermission:
            pass  # audited by intent_score; shown as an explicit "no permission" below
    if intent["status"] != "ok":
        step.event(_notice("intent_denied"))

    profile = state.get("profile")
    fit = None
    if profile:
        value, reasons = fit_score(CompanyProfile.model_validate(profile))
        fit = {"score": value, "reasons": reasons}
    elif (state.get("web") or {}).get("error"):
        step.event(_notice("web_failed"))

    contacts: list[dict] = []
    if account is not None and intent["status"] == "ok":
        env = await step.call_tool("crm", "get_account", {"account_id": account["account_id"]})
        acc = (env.data or {}).get("account") if env.ok and isinstance(env.data, dict) else None
        for c in (acc or {}).get("contacts") or []:
            if isinstance(c, dict) and c.get("contact_id"):
                contacts.append({"contact_id": c["contact_id"], "name": c.get("name"), "title": c.get("title")})

    level_tier, playbook = None, None
    if fit is not None and intent["status"] == "ok":
        level_tier = tier(fit["score"], intent["score"])
        sel = select_playbook(level_tier)
        step.raise_to(sel.level, f"playbook:{sel.ref}")  # discount limits are commercial secrets (L3)
        playbook = {"ref": sel.ref, "tier": sel.tier, "goal": sel.goal, "cadence_days": sel.cadence_days,
                    "max_touches_per_month": sel.max_touches_per_month, "channels": list(sel.channels),
                    "next_steps": list(sel.next_steps), "allowed_commitments": list(sel.allowed_commitments),
                    "needs_approval": list(sel.needs_approval),
                    "forbidden_commitments": list(sel.forbidden_commitments)}
    steps = list(state.get("steps") or []) + [{"step": "intent", "route": "code", "level": int(Level.L3)}]
    return step.update(account=account if intent["status"] == "ok" else None, intent=intent, fit=fit,
                       contacts=contacts, tier=level_tier, playbook=playbook, steps=steps)


def _profile_facts(profile: dict | None) -> dict:
    return {k: profile.get(k) for k in PROMPT_FIELDS} if profile else {}


@node(caps={"sensitive_data"})
async def strategy(state: SalesState, runtime: Any) -> dict:
    step = Step(runtime, state)
    steps = list(state.get("steps") or [])
    account, playbook = state.get("account"), state.get("playbook")
    text = ""
    if account and playbook:
        payload = {"account": account["name"], "tier": state.get("tier"),
                   "fit": (state.get("fit") or {}).get("score"), "fit_reasons": (state.get("fit") or {}).get("reasons"),
                   "intent": (state.get("intent") or {}).get("score"),
                   "intent_reasons": (state.get("intent") or {}).get("reasons"),
                   "profile_facts": _profile_facts(state.get("profile")),
                   "playbook": {k: playbook[k] for k in ("goal", "cadence_days", "max_touches_per_month", "channels",
                                                         "next_steps", "allowed_commitments", "needs_approval",
                                                         "forbidden_commitments")}}
        res = await step.llm([{"role": "system", "content": STRATEGY_SYSTEM},
                              {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                             purpose=_fake.STRATEGY, max_tokens=700)
        text = (res.text or "").strip()
        steps.append({"step": "strategy", "route": str(res.route), "level": int(res.level)})
    elif (state.get("intent") or {}).get("status") != "ok":
        text = NOTICES["intent_denied"]["en"]
    else:
        text = NOTICES["web_failed"]["en"]
    web_meta = state.get("web") or {}
    data = {
        "kind": "sales_profile",
        "company": state.get("company"),
        "account": account,
        "profile": state.get("profile"),
        "web": {k: web_meta.get(k) for k in ("route", "level", "public_only", "pages_used", "flagged_urls",
                                             "dropped_fields", "ignored_tool_calls", "error")},
        "fit": state.get("fit"),
        "intent": state.get("intent"),
        "tier": state.get("tier"),
        "playbook": playbook,
        "contacts": state.get("contacts") or [],
        "steps": steps,
        "followup": {"available": bool(account and playbook and state.get("contacts"))},
    }
    return step.update(steps=steps, output=_out(text, data))


@node(caps={"sensitive_data"})
async def draft_email(state: SalesState, runtime: Any) -> dict:
    step = Step(runtime, state)
    account, level_tier = state.get("account") or {}, state.get("tier") or "low"
    env = await step.call_tool("crm", "get_account", {"account_id": account.get("account_id", "")})
    acc = (env.data or {}).get("account") if env.ok and isinstance(env.data, dict) else None
    contacts = [c for c in (acc or {}).get("contacts") or [] if isinstance(c, dict) and c.get("email")]
    want = state.get("contact_choice")
    contact = next((c for c in contacts if c.get("contact_id") == want), None) if want else \
        (contacts[0] if contacts else None)
    if acc is None or contact is None:
        step.event(_notice("intent_denied"))
        return step.update(draft=None, output=_out(NOTICES["intent_denied"]["en"],
                                                   {"kind": "sales_followup", "refused": "no_contact"}))
    sel = select_playbook(level_tier)
    step.raise_to(sel.level, f"playbook:{sel.ref}")
    payload = {"account": acc.get("name"), "contact_name": contact.get("name"), "contact_title": contact.get("title"),
               "tier": level_tier, "playbook": {"goal": sel.goal, "next_steps": list(sel.next_steps),
                                                "allowed_commitments": list(sel.allowed_commitments),
                                                "forbidden_commitments": list(sel.forbidden_commitments),
                                                "channels": list(sel.channels)}}
    res = await step.llm([{"role": "system", "content": FOLLOWUP_SYSTEM},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                         purpose=_fake.FOLLOWUP, json_mode=True, max_tokens=600)
    parsed = _email.parse_draft(res.text or "")
    source = "model"
    if parsed is None:
        source = "template"  # the model's draft failed the checks: a plain draft built by code
        parsed = _email.template(account=str(acc.get("name") or ""), contact_name=contact.get("name"),
                                 next_step=sel.next_steps[0] if sel.next_steps else "a short call")
    draft = {"account_id": acc.get("account_id"), "contact_id": contact["contact_id"],
             "contact_name": contact.get("name"), "to": contact["email"], "subject": parsed[0], "body": parsed[1],
             "source": source, "playbook": sel.ref}
    steps = [{"step": "followup_draft", "route": str(res.route), "level": int(res.level)}]
    return step.update(draft=draft, steps=steps)


@node(caps={"sensitive_data", "external_action"})
async def propose_email(state: SalesState, runtime: Any) -> dict:
    step = Step(runtime, state)
    d = state.get("draft") or {}
    env = await step.call_tool("crm", "send_email", {"account_id": d.get("account_id"), "to": d.get("to"),
                                                     "subject": d.get("subject"), "body": d.get("body")})
    data = {"kind": "sales_followup", "draft": {k: d.get(k) for k in ("contact_id", "contact_name", "subject",
                                                                      "body", "source", "playbook")},
            "proposal_id": env.proposal_id, "tier": env.tier, "steps": state.get("steps") or []}
    if not env.proposal_id:
        # explicit: the write tool refused (no permission, recipient not a contact, ...)
        data["refused"] = env.error or "not_filed"
        return step.update(proposal_id=None, output=_out(env.message or "The e-mail proposal was not filed.", data))
    text = "The draft is waiting for approval by the head of sales; nothing has been sent."
    return step.update(proposal_id=env.proposal_id, output=_out(text, data))


@node(caps=set(), gate=True)
async def wait(state: SalesState, runtime: Any) -> dict:
    return {"decision": await approval_gate(runtime, state["proposal_id"])}


@node(caps={"sensitive_data"})
async def after(state: SalesState, runtime: Any) -> dict:
    d = state.get("decision") or {}
    out = dict(state.get("output") or _out("", {}))
    data = dict(out.get("data") or {})
    data["decision"] = {"status": d.get("status"), "approved": bool(d.get("approved"))}
    text = ("The follow-up e-mail was approved and sent (simulated outbox)." if d.get("approved")
            else f"The follow-up e-mail was not sent ({d.get('status') or 'not approved'}).")
    return {"output": _out(text, data)}


def _after_intake(state: SalesState) -> str:
    return {"profile": "web", "followup": "draft_email"}.get(state.get("mode") or "", "end")


def _after_draft(state: SalesState) -> str:
    return "propose_email" if state.get("draft") else "end"


def _after_propose(state: SalesState) -> str:
    return "wait" if state.get("proposal_id") else "end"


def build_graph() -> StateGraph:
    """The uncompiled graph; the router compiles it with the checkpointer."""
    _fake.install()
    g = StateGraph(SalesState, context_schema=RunContext)
    for name, fn in (("intake", intake), ("web", web), ("score", score), ("strategy", strategy),
                     ("draft_email", draft_email), ("propose_email", propose_email), ("wait", wait),
                     ("after", after)):
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _after_intake, {"web": "web", "draft_email": "draft_email", "end": END})
    g.add_edge("web", "score")
    g.add_edge("score", "strategy")
    g.add_edge("strategy", END)
    g.add_conditional_edges("draft_email", _after_draft, {"propose_email": "propose_email", "end": END})
    g.add_conditional_edges("propose_email", _after_propose, {"wait": "wait", "end": END})
    g.add_edge("wait", "after")
    g.add_edge("after", END)
    return g
