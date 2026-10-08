"""review_contract: Clause Check's per-clause review, run as the requesting user through the gateway.

Flow (all code except the per-clause model call):
  start check -> contract row (RLS) -> clause library (RLS) -> hwm = max(L3, playbook, row, text)
  -> split (vendored) -> per clause: vendored bounded loop with GatewayBackend (read-only tools)
  -> map Clause Check's accept/flag/redline + position to acceptable/fallback/unacceptable
  -> code: required terms that are missing, embedded instructions, quote verification,
     suggestions from legal.clause_library -> store in legal.reviews (SECURITY DEFINER).

The contract is untrusted input. Nothing in this module can write a business table, create a
proposal, call an MCP tool or send anything; the stored review is the platform's own record of
its output, written through `legal.save_review`, which takes the requester from the session.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import anyio.to_thread
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from corpai.classify import classify_text
from corpai.db import pg
from corpai.gateway import get_gateway
from corpai.ids import new_id
from corpai.legal.backend import GatewayBackend, KbVerdict
from corpai.legal.clausecheck.config import Settings
from corpai.legal.clausecheck.models import ClauseReview, Contract
from corpai.legal.clausecheck.review import model_review
from corpai.legal.clausecheck.split import contract_from_text
from corpai.legal.injection import InstructionHit, find_embedded_instructions
from corpai.legal.library import clause_library, contract_row, library_choice, require_start
from corpai.legal.playbook import (
    KbPlaybook,
    KbRule,
    RuleAssessment,
    assess_clause,
    load_playbook,
    missing_rules,
    relevant_rules,
)
from corpai.legal.quotes import QuoteSpan, region_of, verified
from corpai.legal.results import ClauseResult, Finding, ReviewResult, Suggestion
from corpai.types import Level, Principal, Route

MAX_TEXT_CHARS = 200_000
# Each clause costs one to three model calls; a pathological upload must not drain the budget.
MAX_CLAUSES = 120
RISK_ORDER = {"high": 0, "medium": 1, "low": 2}
_VERDICT_RANK = {"acceptable": 0, "fallback": 1, "unacceptable": 2}
EMBEDDED_EXPLANATION = ("This clause contains text addressed to an AI system rather than to the other party. "
                        "It was treated as contract data and had no effect on the review. A lawyer must read "
                        "the clause and the counterparty must be asked to remove that text.")


def _overlaps(region: tuple[int, int] | None, hit: InstructionHit) -> bool:
    return bool(region) and hit.start < region[1] and hit.end > region[0]


class ReviewRouteError(RuntimeError):
    """A legal review call was not routed local (must never happen: hwm >= L3)."""


def _extension(final: dict | None) -> dict[str, Any]:
    """position / quotes / embedded_instruction from the model's last answer, validated; {} if unusable."""
    if not isinstance(final, dict):
        return {}
    try:
        v = KbVerdict.model_validate({**final, "clause_id": str(final.get("clause_id") or "?")})
    except ValidationError:
        # Keep what can be validated on its own; a malformed field is simply absent.
        out: dict[str, Any] = {}
        if final.get("position") in ("preferred", "fallback", "outside", "not_applicable"):
            out["position"] = final["position"]
        if isinstance(final.get("quotes"), list):
            out["quotes"] = [q for q in final["quotes"] if isinstance(q, str)][:5]
        if isinstance(final.get("embedded_instruction"), bool):
            out["embedded_instruction"] = final["embedded_instruction"]
        return out
    return {"position": v.position, "quotes": list(v.quotes)[:5], "embedded_instruction": v.embedded_instruction}


def _suggest(rule: KbRule | None, library: list[dict], verdict: str) -> tuple[Suggestion | None, str | None]:
    if verdict == "acceptable" or rule is None or not rule.clause_type:
        return None, None
    if not library:
        return None, "no clause library wording available to you"
    # unacceptable -> the compromise we can live with first; missing / fallback -> our preferred wording
    order = ("fallback", "preferred") if verdict == "unacceptable" else ("preferred",)
    for pos in order:
        row = library_choice(library, rule.clause_type, pos)
        if row:
            return Suggestion(clause_id=row["clause_id"], clause_type=row["clause_type"], position=pos,
                              text=row["text"]), None
    return None, f"the clause library has no wording for {rule.clause_type}"


def _code_floor(playbook: KbPlaybook, cr: ClauseReview) -> RuleAssessment | None:
    """The worst placement the playbook's own checks (never-accept wording, numeric limits, fallback
    wording) give this clause, or None when they find nothing below preferred. A model that was
    taken over, or simply wrong, cannot lift a clause above this: "unlimited liability" stays
    unacceptable whatever the model answers."""
    found = assess_clause(playbook, cr.clause.text, cr.clause.section_path)
    worst = found[0] if found else None
    return worst if worst is not None and worst.position in ("outside", "fallback") else None


def _clause_result(text: str, cr: ClauseReview, final: dict | None, playbook: KbPlaybook, library: list[dict],
                   hits: list[InstructionHit], *, quote_regions: list[tuple[int, int]] | None = None,
                   forbidden: list[tuple[int, int]] | None = None,
                   ) -> tuple[ClauseResult, int, tuple[int, int] | None, bool]:
    v = cr.verdict
    ext = _extension(final)
    rule_ids = [r for r in dict.fromkeys(v.rule_ids) if playbook.get(r)]
    if not rule_ids:
        topic_rules = playbook.for_topic(v.topic)
        rule_ids = [topic_rules[0].id] if topic_rules else ["KB-00"]
    region = region_of(text, cr.clause.text)
    candidates = [*ext.get("quotes", []), v.anchor_text, cr.redline.matched_text if cr.redline else None]
    # A quote must come from this clause (or a clause the review really fetched with get_clause),
    # never from elsewhere in the contract and never from an embedded instruction outside it.
    regions = quote_regions if quote_regions is not None else ([region] if region else [])
    quotes, rejected = verified(text, candidates, regions=regions, forbidden=forbidden or [])
    embedded = bool(hits) or bool(ext.get("embedded_instruction"))
    position = ext.get("position")
    note = ""
    if embedded:
        verdict = "unacceptable"
    elif (v.verdict == "flag" and cr.trace.model_verdict == "accept" and cr.trace.marks
          and position != "outside"):
        # The model accepted the clause but also called mark_for_review, which the vendored loop
        # turns into flag. Live eval 2026-10-08: the real model does this to ask a lawyer to confirm
        # something it cannot see (a referenced DPA), not because the clause is outside the
        # playbook. Not acceptable (a person must look), not unacceptable either. Accept + outside
        # is a contradiction and falls through to unacceptable below.
        verdict = "fallback"
        note = f" (The reviewer accepted the clause but asked a lawyer to confirm: {cr.trace.marks[0][:300]})"
    elif v.verdict in ("flag", "redline"):
        verdict = "unacceptable"
    elif position == "fallback":
        verdict = "fallback"
    elif position == "outside":
        verdict = "unacceptable"  # the model contradicts itself; a human decides
        note = " (The reviewer accepted the clause but placed it outside the playbook.)"
    elif position is None:
        verdict = "fallback"  # accept without a stated position: not enough to call it preferred
        note = " (The reviewer did not state the playbook position; treated as fallback.)"
    else:
        verdict = "acceptable"
    risk = v.risk_level
    floor = _code_floor(playbook, cr)
    if floor is not None:
        want = "unacceptable" if floor.position == "outside" else "fallback"
        if _VERDICT_RANK[want] > _VERDICT_RANK[verdict]:
            verdict = want
            note += (f" (Code check against playbook rule {floor.rule.id}: {floor.reason}. The reviewer's verdict "
                     "was raised to this; a lawyer must confirm.)")
            rule_ids = [floor.rule.id, *(r for r in rule_ids if r != floor.rule.id)]
            if want == "unacceptable" and RISK_ORDER.get(floor.rule.severity, 2) < RISK_ORDER.get(risk, 2):
                risk = floor.rule.severity
        elif want == "unacceptable" and floor.rule.id not in rule_ids:
            # Unacceptable for another reason (e.g. an embedded instruction): still name the playbook
            # rule the clause breaks, so the lawyer gets the right fallback wording.
            rule_ids = [floor.rule.id, *rule_ids]
    primary = playbook.get(rule_ids[0])
    suggestion, s_note = _suggest(primary, library, verdict)
    redline = None
    if cr.redline is not None and cr.redline.located:
        redline = cr.redline.model_dump(include={"anchor_text", "replacement", "located", "matched_text", "diff"})
    result = ClauseResult(
        clause_id=cr.clause.clause_id, heading=cr.clause.heading, topic=v.topic, verdict=verdict,
        rule=playbook.rule_ref(rule_ids[0]), rules=[playbook.rule_ref(r) for r in rule_ids],
        risk="high" if embedded else risk, confidence="low" if embedded else v.confidence,
        quotes=quotes, suggestion=suggestion, suggestion_note=s_note,
        explanation=EMBEDDED_EXPLANATION if embedded else (v.rationale + note),
        embedded_instruction=embedded, redline=None if embedded else redline,
        tool_calls=list(cr.trace.tool_calls), error=cr.trace.error)
    return result, rejected, region, bool(ext.get("embedded_instruction"))


def _host(rule: KbRule, results: list[ClauseResult], clauses: dict[str, tuple[str, str]],
          playbook: KbPlaybook) -> ClauseResult | None:
    """The clause that should have carried the missing terms: chosen by code (playbook keywords
    over the clause text and heading), so a misled model cannot hide the gap by mislabelling topics.

    An unacceptable clause keeps its own verdict and the gap gets an entry of its own, unless the
    reviewer found that clause unacceptable under this very rule: then it carries the gap, one entry
    for one problem (live eval 2026-10-08: the real model rejects "Each party complies with
    applicable data protection law" under KB-03, which is exactly the missing Article 28 terms)."""
    same = [r for r in results if r.verdict == "unacceptable" and not r.embedded_instruction
            and r.clause_id in clauses and (r.rule or "").split("@")[0] == rule.id]
    candidates = same or [r for r in results if r.verdict != "unacceptable" and r.clause_id in clauses]
    best: tuple[int, ClauseResult] | None = None
    for r in candidates:
        body, path = clauses[r.clause_id]
        score = next((sc for rr, sc in relevant_rules(playbook, body, path) if rr.id == rule.id), 0)
        if score and (best is None or score > best[0]):
            best = (score, r)
    if best is not None:
        return best[1]
    if same:
        return same[0]
    return next((r for r in results if r.topic == rule.topic and r.clause_id is not None
                 and r.verdict != "unacceptable"), None)


def _apply_missing(results: list[ClauseResult], missing: list[KbRule], playbook: KbPlaybook,
                   library: list[dict], clauses: dict[str, tuple[str, str]]) -> None:
    """A required term that is absent marks the clause on its subject (or a new entry) as missing."""
    for rule in missing:
        host = _host(rule, results, clauses, playbook)
        why = f"Required terms are missing: {rule.title} (rule {rule.id})."
        suggestion, s_note = _suggest(rule, library, "missing")
        if host is not None:
            host.verdict = "missing"
            host.rule = playbook.rule_ref(rule.id)
            if host.rule not in host.rules:
                host.rules.insert(0, host.rule)
            host.risk = rule.severity  # type: ignore[assignment]
            host.explanation = f"{why} {host.explanation}".strip()
            host.suggestion, host.suggestion_note = suggestion, s_note
            host.redline = None
        else:
            results.append(ClauseResult(
                clause_id=None, heading=rule.title, topic=rule.topic, verdict="missing",
                rule=playbook.rule_ref(rule.id), rules=[playbook.rule_ref(rule.id)], risk=rule.severity,  # type: ignore[arg-type]
                confidence="high", explanation=why, suggestion=suggestion, suggestion_note=s_note))


def _summary(results: list[ClauseResult], findings: list[Finding], calls: list, rejected: int) -> dict:
    counts = {k: 0 for k in ("acceptable", "fallback", "unacceptable", "missing")}
    for r in results:
        counts[r.verdict] += 1
    attention = sorted((r for r in results if r.verdict in ("unacceptable", "missing")),
                       key=lambda r: (RISK_ORDER[r.risk], r.clause_id or "~"))
    bad = [r for r in results if r.verdict in ("unacceptable", "missing")]
    if findings or any(r.risk == "high" for r in bad):
        overall = "high"
    elif bad:
        overall = "medium"
    else:
        overall = "low"
    return {
        "clauses_reviewed": sum(1 for r in results if r.clause_id is not None),
        "counts": counts,
        "needs_attention": [r.clause_id or r.heading for r in attention],
        "overall_risk": overall,  # computed by code from the verdicts, never by the model
        "embedded_instructions": len(findings),
        "errors": [r.clause_id for r in results if r.error],
        "model_calls": len(calls),
        "routes": sorted({c.route for c in calls}),
        "audit_seqs": [c.audit_seq for c in calls],
        "unverified_quotes": rejected,
    }


def _findings(text: str, hits: list[InstructionHit], results: list[ClauseResult],
              regions: dict[str, tuple[int, int] | None], model_flags: dict[str, bool]) -> list[Finding]:
    out: list[Finding] = []
    covered: set[str] = set()
    for h in hits:
        cid = next((c for c, reg in regions.items() if _overlaps(reg, h)), None)
        model_too = bool(cid and model_flags.get(cid))
        out.append(Finding(kind="embedded_instruction", clause_id=cid,
                           quote=QuoteSpan(start=h.start, end=h.end, text=h.text), patterns=list(h.kinds),
                           detected_by="code+model" if model_too else "code"))
        if cid:
            covered.add(cid)
    for r in results:
        if r.clause_id and model_flags.get(r.clause_id) and r.clause_id not in covered:
            # reported by the model only: quote what it quoted, or the clause itself
            reg = regions.get(r.clause_id)
            quote = r.quotes[0] if r.quotes else (
                QuoteSpan(start=reg[0], end=min(reg[1], reg[0] + 600), text=text[reg[0]:min(reg[1], reg[0] + 600)])
                if reg else None)
            if quote is not None:
                out.append(Finding(kind="embedded_instruction", clause_id=r.clause_id, quote=quote,
                                   detected_by="model"))
    return out


def _consulted_regions(text: str, contract: Contract, cr: ClauseReview) -> list[tuple[int, int]]:
    """Regions of the clauses this review fetched with get_clause (from the tool trace recorded by
    code, not the model's own consulted_clauses claim). A bare section title stands for its children."""
    out: list[tuple[int, int]] = []
    for call in cr.trace.tool_calls:
        if not call.startswith("get_clause:"):
            continue
        cid = call.split(":", 1)[1]
        if cid == cr.clause.clause_id:
            continue
        for c in contract.clauses:
            if (c.clause_id == cid or c.clause_id.startswith(cid + ".")) and c.text.strip():
                reg = region_of(text, c.text)
                if reg:
                    out.append(reg)
    return out


async def _separate_dpa_signed(principal: Principal, row: dict, run_id: str) -> bool:
    """A signed DPA with the same customer that this reviewer can see (RLS)."""
    if not row.get("account_id"):
        return False
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        cur = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM legal.contracts WHERE account_id = %s AND contract_type = 'DPA' "
            "AND status = 'signed' AND contract_id <> %s) AS found", (row["account_id"], row["contract_id"]))
        found = await cur.fetchone()
    return bool(found and found["found"])


async def _store(principal: Principal, run_id: str, result: ReviewResult) -> None:
    payload = result.model_dump(mode="json", exclude={"stored"})
    async with pg.principal_tx(principal, run_id=run_id) as conn:
        await conn.execute(
            "SELECT legal.save_review(%s, %s, %s, %s, %s::smallint, %s)",
            (result.review_id, result.contract_id, result.playbook_ref, Jsonb(payload), result.level, run_id))


async def review_contract(principal: Principal, *, text: str, contract_id: str | None, run_id: str,
                          gateway: Any = None, store: bool = True) -> ReviewResult:
    """Review a contract clause by clause against ABC Tech's playbook. See the module docstring.

    Raises PermissionError (may not start legal_review; contract not visible), ValueError (bad
    input), and the gateway's errors (LocalUnavailable, BudgetExceeded, ...) unchanged.
    """
    if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
        raise ValueError("run_id must be a non-empty string")
    await require_start(principal, run_id)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the contract text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"the contract is too long (max {MAX_TEXT_CHARS} characters)")
    playbook = load_playbook()
    row = None
    if contract_id is not None:
        row = await contract_row(principal, contract_id, run_id)
        if row is None:
            raise PermissionError("contract not found or no permission to read it")
    library = await clause_library(principal, run_id)

    # hwm from code: the playbook (L3), the stored contract's label, and the text's own content.
    hwm = Level(max(int(Level.L3), int(playbook.level), int(row["level"]) if row else 1,
                    int(classify_text(text).level)))
    contract: Contract = contract_from_text(text, name=contract_id or "uploaded")
    if len(contract.reviewable) > MAX_CLAUSES:
        raise ValueError(f"the contract has too many clauses (max {MAX_CLAUSES}); split it")
    settings = Settings(mode="gateway")
    gw = gateway or get_gateway()

    def _run_all() -> list[tuple[ClauseReview, dict | None, list]]:
        out = []
        for clause in contract.reviewable:
            backend = GatewayBackend(contract, principal=principal, run_id=run_id, hwm=hwm, gateway=gw)
            review = model_review(backend, contract, clause, playbook.clausecheck, settings)
            out.append((review, backend.last_final, list(backend.calls)))
        return out

    reviews = await anyio.to_thread.run_sync(_run_all)

    calls = [c for _, _, cs in reviews for c in cs]
    if any(c.route != str(Route.LOCAL) for c in calls):
        raise ReviewRouteError("a legal review call left the local route")
    hits = find_embedded_instructions(text)
    results: list[ClauseResult] = []
    regions: dict[str, tuple[int, int] | None] = {}
    model_flags: dict[str, bool] = {}
    rejected = 0
    for cr, final, _ in reviews:
        region = region_of(text, cr.clause.text)
        in_clause = [h for h in hits if _overlaps(region, h)]
        res, rej, reg, flagged = _clause_result(
            text, cr, final, playbook, library, in_clause,
            quote_regions=[*([region] if region else []), *_consulted_regions(text, contract, cr)],
            forbidden=[(h.start, h.end) for h in hits if h not in in_clause])
        results.append(res)
        regions[cr.clause.clause_id] = reg
        model_flags[cr.clause.clause_id] = flagged
        rejected += rej
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    matches_stored = bool(row) and row["text_sha256"] == text_sha256
    # Art. 28 terms may sit in a separate signed DPA with the same customer, or (for the stored text
    # itself) in a clause the contract store records: the same facts the contracts_missing_dpa
    # metric and the missing_dpa_clause signal use, so M2 and M3 do not contradict each other. A
    # counterparty's marked-up copy is judged on its own text (it may have dropped the DPA clause).
    dpa_in_place = bool(row) and ((matches_stored and bool(row["has_dpa_clause"]))
                                  or await _separate_dpa_signed(principal, row, run_id))
    missing = missing_rules(playbook, text, contract_type=row["contract_type"] if row else None,
                            personal_data=row["processes_personal_data"] if row else None,
                            dpa_in_place=dpa_in_place)
    clause_texts = {cr.clause.clause_id: (cr.clause.text, cr.clause.section_path) for cr, _, _ in reviews}
    _apply_missing(results, missing, playbook, library, clause_texts)
    findings = _findings(text, hits, results, regions, model_flags)
    level = max([int(hwm), *(c.level for c in calls)])
    result = ReviewResult(
        review_id=new_id("rev"), contract_id=contract_id, run_id=run_id, requested_by=principal.user_id,
        playbook_ref=playbook.ref, level=level, text_sha256=text_sha256,
        clauses=results, findings=findings, summary=_summary(results, findings, calls, rejected))
    # A lawyer may review a counterparty's marked-up version of a stored contract; say so explicitly.
    result.summary["matches_stored_text"] = matches_stored if row else None
    if store:
        await _store(principal, run_id, result)
        result.stored = True
    return result


async def list_reviews(principal: Principal, *, contract_id: str | None = None, limit: int = 50) -> list[dict]:
    """Stored reviews the principal may read (RLS: requester, head of legal, the contract's handler)."""
    limit = max(1, min(int(limit), 200))
    sql = ("SELECT review_id, contract_id, requested_by, run_id, playbook_ref, created_at, level, "
           "result->'summary' AS summary FROM legal.reviews")
    params: list[Any] = []
    if contract_id is not None:
        sql += " WHERE contract_id = %s"
        params.append(contract_id)
    sql += " ORDER BY created_at DESC, review_id DESC LIMIT %s"
    params.append(limit)
    async with pg.principal_tx(principal) as conn:
        cur = await conn.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]


async def get_review(principal: Principal, review_id: str) -> ReviewResult | None:
    async with pg.principal_tx(principal) as conn:
        cur = await conn.execute("SELECT result FROM legal.reviews WHERE review_id = %s", (review_id,))
        row = await cur.fetchone()
    if row is None:
        return None
    data = row["result"] if isinstance(row["result"], dict) else json.loads(row["result"])
    return ReviewResult.model_validate({**data, "stored": True})
