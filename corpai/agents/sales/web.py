"""sales_web: the isolated web sub-agent of sales_profile (design-review 二.6, decision D5).

    search    web.search(company)           the query is the company name only (code built it)
    fetch     web.fetch(url) per result     the page dicts are kept exactly as the tool returned them
    extract   corpai.sales.extract_profile_with_meta(pages)   one tool-less model call, fixed schema out

It runs as its own agent, `sales_web` (a RunContext copy whose agent is sales_web, so every tool
token's act claim is agent:sales_web, which holds web.search and web.fetch and nothing else). Every
node declares caps {untrusted_input} only: no CRM data, no write or outbound tool, nothing of the
parent's state comes in except the company name.

The fetched pages go to the extractor unchanged: its public_only decision compares every prompt
field byte for byte with the published corpus, so an edit here would drop the call to the local
route. The graph is compiled with checkpointer=False: page text never reaches a checkpoint, and
run_web_research() hands back only the validated CompanyProfile and metadata without text.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from langgraph.graph import END, START, StateGraph

from corpai.agents.runtime import BaseState, RunContext, Step, current_hwm, node
from corpai.sales import NoPermission, extract_profile_with_meta
from corpai.types import Level

AGENT = "sales_web"
MAX_PAGES = 12  # corpai.sales.extract keeps at most 12 pages per prompt as well

FLAG_NOTICE = {"zh": "这个网页里藏有针对 AI 的指令，已被标记并忽略：它只能影响固定字段，不能调用任何工具。",
               "en": "This page carries an instruction aimed at AI; it was flagged and ignored: it can only "
                     "reach fixed fields and cannot call any tool."}


class WebState(BaseState, total=False):
    company: str
    urls: list[str]
    pages: list[dict]  # web.fetch page dicts, unchanged; this state is never checkpointed
    profile: dict | None
    meta: dict | None
    error: str | None


@node(caps={"untrusted_input"})
async def search(state: WebState, runtime: Any) -> dict:
    step = Step(runtime, state)
    env = await step.call_tool("web", "search", {"company": state.get("company") or ""})
    if not env.ok:
        return step.update(urls=[], error=str(env.error or "search_failed"))
    results = (env.data or {}).get("results") if isinstance(env.data, dict) else None
    urls = [str(r["url"]) for r in results or [] if isinstance(r, dict) and isinstance(r.get("url"), str)]
    urls = list(dict.fromkeys(urls))[:MAX_PAGES]
    return step.update(urls=urls, error=None if urls else "no_pages")


@node(caps={"untrusted_input"})
async def fetch(state: WebState, runtime: Any) -> dict:
    step = Step(runtime, state)
    pages: list[dict] = []
    for url in state.get("urls") or []:
        env = await step.call_tool("web", "fetch", {"url": url})
        page = (env.data or {}).get("page") if env.ok and isinstance(env.data, dict) else None
        if isinstance(page, dict):
            pages.append(page)  # as returned: url, domain, title, kind, published_on, text
    return step.update(pages=pages, error=None if pages else (state.get("error") or "no_pages"))


def _route_reason(route: str, level: int) -> str:
    # same reading as runtime.llm_update: local at L1/L2 only happens as the external_down fallback
    return "external_down_fallback" if route == "local" and level <= 2 else "route_by_level"


@node(caps={"untrusted_input"})
async def extract(state: WebState, runtime: Any) -> dict:
    ctx: RunContext = runtime.context
    step = Step(runtime, state)
    pages = state.get("pages") or []
    if not pages:
        return step.update(profile=None, meta=None, error=state.get("error") or "no_pages")
    try:
        # account_id only filters pages tagged with another account; fetched pages carry no tag.
        # The user's raised level is a floor for this call too (design-review 四.2 rule 3): at L3/L4
        # the extraction runs local even though the pages are public.
        floor = Level(max(1, min(4, int(ctx.user_level))))
        profile, meta = await extract_profile_with_meta(ctx.principal, account_id="", pages=pages,
                                                        run_id=ctx.run_id, hwm=floor)
    except NoPermission:
        return step.update(profile=None, meta=None, error="no_permission")
    except ValueError:
        return step.update(profile=None, meta=None, error="no_published_pages")
    step.event({"t": "route", "route": meta.route, "level": int(meta.level),
                "reason": _route_reason(meta.route, int(meta.level))})
    for url in meta.flagged_urls:
        step.event({"t": "notice", "code": "embedded_instruction", "url": url, **FLAG_NOTICE})
    info = {"route": meta.route, "level": int(meta.level), "public_only": bool(meta.public_only),
            "pages_used": list(meta.pages_used), "flagged_urls": list(meta.flagged_urls),
            "dropped_fields": list(meta.dropped_fields), "ignored_tool_calls": int(meta.ignored_tool_calls)}
    return step.update(profile=profile.model_dump(mode="json"), meta=info, error=None)


def build_sales_web_graph() -> StateGraph:
    g = StateGraph(WebState, context_schema=RunContext)
    g.add_node("search", search)
    g.add_node("fetch", fetch)
    g.add_node("extract", extract)
    g.add_edge(START, "search")
    g.add_edge("search", "fetch")
    g.add_edge("fetch", "extract")
    g.add_edge("extract", END)
    return g


_COMPILED: Any = None


def _compiled() -> Any:
    global _COMPILED
    if _COMPILED is None:
        # False, not None: None would inherit the parent run's checkpointer and store page text
        _COMPILED = build_sales_web_graph().compile(checkpointer=False)
    return _COMPILED


# Events that may leave the sub-agent: tool calls (fixed denial texts only), the extraction's route,
# the flagged-page notices. Its hwm steps stay inside: page content does not leave, only the profile.
_KEEP = ("tool", "route", "notice")


async def run_web_research(ctx: RunContext, company: str) -> dict:
    """Run the sub-agent for `company` as agent sales_web. Returns {profile, meta, error, events,
    web_hwm}: no page text."""
    web_ctx = replace(ctx, agent=AGENT)
    out = await _compiled().ainvoke({"company": company, "urls": [], "pages": [], "profile": None, "meta": None,
                                     "error": None}, {"recursion_limit": 20}, context=web_ctx)
    # tagged so the timeline shows them as the sub-agent's (its tool levels are not the parent's hwm)
    events = [{**e, "agent": AGENT} for e in out.get("events") or []
              if e.get("t") in _KEEP and e.get("code") != "local_mode"]
    return {"profile": out.get("profile"), "meta": out.get("meta"), "error": out.get("error"), "events": events,
            "web_hwm": int(current_hwm(out))}
