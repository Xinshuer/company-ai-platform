"""Deterministic FakeLLM responders for the legal purposes (CORPAI_LLM=fake: tests, CI, offline demo).

`legal.review` behaves like a careful model: a rule-based reviewer in the spirit of Clause Check's
mock mode, driven by ABC Tech's playbook. It reads the clause from the same prompt a real model
gets, asks for cross-referenced clauses with get_clause (so the trap case exercises the tool loop),
and answers with schema-valid verdict JSON. It reports embedded instructions instead of obeying them.

`legal.draft` fills template variables from the lawyer's instructions with a few regexes and picks
a fallback only when the instructions ask for one.

`compat.legal_review` (opt-in, `register_compat_responder`) answers the unchanged Clause Check
OpenAICompatBackend that reaches the gateway through /v1/chat/completions.
"""

from __future__ import annotations

import json
import re
from typing import Any

from corpai.gateway import fake_llm
from corpai.legal.clausecheck.redline import split_sentences
from corpai.legal.injection import find_embedded_instructions
from corpai.legal.playbook import KbPlaybook, assess_clause, assess_rule, load_playbook

_CLAUSE_RE = re.compile(r'<clause id="([^"]+)">\n(.*?)\n</clause>', re.S)
_PATH_RE = re.compile(r"^Review clause \S+ \((.*)\)\.$", re.M)
_REFS_RE = re.compile(r"^Cross-references detected by the parser: (.*)\.$", re.M)


def _first_user(messages: list[dict]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _sentence(text: str, span: tuple[int, int] | None) -> str:
    sents = split_sentences(text)
    if span is not None:
        needle = text[span[0]:span[1]].lower()
        for s in sents:
            if needle and needle in s.lower():
                return s
    return sents[0] if sents else text.strip()[:300]


def _refs(user: str) -> list[str]:
    m = _REFS_RE.search(user)
    if not m or m.group(1).strip() == "none":
        return []
    return [part.strip().split(" ")[0] for part in m.group(1).split(", ") if part.strip()]


def assess_for_model(playbook: KbPlaybook, clause_id: str, text: str, section_path: str,
                     ref_texts: dict[str, str]) -> dict[str, Any]:
    """A KbVerdict-shaped dict for one clause (deterministic)."""
    hits = find_embedded_instructions(text)
    assessments = assess_clause(playbook, text, section_path)
    worst = assessments[0] if assessments else None
    rule = worst.rule if worst else playbook.get("KB-00")
    cc_rule = playbook.clausecheck.get(rule.id)
    base: dict[str, Any] = {
        "clause_id": clause_id, "topic": rule.topic, "rule_ids": [rule.id], "confidence": "medium",
        "anchor_text": None, "replacement": None, "consulted_clauses": sorted(ref_texts),
        "embedded_instruction": bool(hits),
    }
    if hits:
        quote = hits[0].text
        # KB-00 never accepts instruction-like text, so the clause is outside the playbook whatever
        # its other terms say.
        return {**base, "verdict": "flag", "risk_level": "high", "position": "outside",
                "rule_ids": list(dict.fromkeys([rule.id, "KB-00"])),
                "quotes": [quote[:300]], "confidence": "low",
                "rationale": (f"Clause {clause_id} contains text addressed to an AI system rather than contract "
                              "language. It was treated as data and does not change the assessment; a lawyer "
                              "must read the clause.")}
    if worst is not None and worst.position == "outside":
        sentence = _sentence(text, worst.span)
        rationale = (f'Clause {clause_id} says "{sentence[:160]}", which is outside playbook rule {rule.id} '
                     f"({rule.title}): {worst.reason}.")
        out = {**base, "verdict": "flag", "risk_level": rule.severity, "position": "outside",
               "quotes": [sentence], "rationale": rationale}
        if cc_rule is not None and cc_rule.preferred_language:
            out.update(verdict="redline", anchor_text=sentence, replacement=cc_rule.preferred_language)
        return out
    for ref, rtext in sorted(ref_texts.items()):
        for a in assessments:
            ra = assess_rule(a.rule, rtext)
            if ra.position == "outside":
                return {**base, "topic": a.rule.topic, "rule_ids": [a.rule.id], "verdict": "flag",
                        "risk_level": a.rule.severity, "position": "outside",
                        "quotes": [_sentence(text, None)],
                        "rationale": (f"Clause {clause_id} looks acceptable on its face, but the referenced Section "
                                      f"{ref} undermines it ({ra.reason}; rule {a.rule.id}: {a.rule.title}). "
                                      "A lawyer should read both together.")}
    if worst is not None and worst.position == "fallback":
        sentence = _sentence(text, worst.span)
        return {**base, "verdict": "accept", "risk_level": "low", "position": "fallback", "quotes": [sentence],
                "rationale": (f'Clause {clause_id} ("{sentence[:160]}") is acceptable only under a fallback '
                              f"position of rule {rule.id} ({rule.title}): {worst.reason}.")}
    sentence = _sentence(text, None)
    if worst is not None:
        return {**base, "verdict": "accept", "risk_level": "low", "position": "preferred", "quotes": [sentence],
                "rationale": (f"Clause {clause_id} meets the preferred position of rule {rule.id} "
                              f"({rule.title}).")}
    return {**base, "topic": "other", "verdict": "accept", "risk_level": "low", "position": "not_applicable",
            "quotes": [sentence], "rationale": f"Clause {clause_id} is ordinary boilerplate (rule KB-00)."}


def review_responder(messages: list[dict], *, route: str, json_mode: bool) -> str | dict:
    user = _first_user(messages)
    m = _CLAUSE_RE.search(user)
    if m is None:
        return json.dumps({"error": "no clause in the prompt"})
    clause_id, text = m.group(1), m.group(2)
    pm = _PATH_RE.search(user)
    section_path = pm.group(1) if pm else ""
    ref_texts: dict[str, str] = {}
    asked = False
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            asked = True
        if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
            try:
                data = json.loads(msg["content"])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get("text"), str) and data.get("clause_id"):
                ref_texts[str(data["clause_id"])] = data["text"]
    refs = _refs(user)
    if refs and not asked and not json_mode:
        return {"content": "", "tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": "get_clause", "arguments": json.dumps({"clause_id": r})}}
            for i, r in enumerate(refs)]}
    return json.dumps(assess_for_model(load_playbook(), clause_id, text, section_path, ref_texts))


# ---- drafting -----------------------------------------------------------------------------

_FALLBACK_ASKS = {
    "governing_law": r"\b(german|dutch|french|polish|austrian|italian|spanish|customer'?s?) law\b|seat of the defendant",
    "liability_cap": r"\b(two times|2x|twice)\b|higher cap|sub-?cap",
    "auto_renewal": r"\b(30|thirty|60|sixty)[- ]days?'? notice\b|notice of (30|thirty|60|sixty) days",
    "service_credits": r"\b(20|twenty) ?% (service )?credits?|higher service credits?",
    "data_processing": r"\b(customer'?s?|their) (own )?dpa\b",
}
_EXTRACT = {
    "fleet_size": (r"\b(\d{1,6})\s+vehicles\b", int),
    "term_months": (r"\b(\d{1,3})[- ]months?\b", int),
    "effective_on": (r"\b(\d{4}-\d{2}-\d{2})\b", str),
    "customer_city": (r"\b(?:based in|in|located in)\s+([A-Z][a-z]+)\b", str),
    "customer_name": (r"\bfor\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,4})", str),
}


def draft_responder(messages: list[dict], *, route: str, json_mode: bool) -> str:
    try:
        req = json.loads(_first_user(messages))
    except json.JSONDecodeError:
        return json.dumps({"variables": {}, "fallbacks": {}})
    instructions = str(req.get("instructions") or "")
    wanted = [v["name"] for v in req.get("fill", []) if isinstance(v, dict) and "name" in v]
    values: dict[str, Any] = {}
    for name in wanted:
        rule = _EXTRACT.get(name)
        if rule is None:
            continue
        m = re.search(rule[0], instructions)
        if m:
            values[name] = rule[1](m.group(1).strip())
    fallbacks: dict[str, str] = {}
    for ctype, options in (req.get("fallback_options") or {}).items():
        ask = _FALLBACK_ASKS.get(ctype)
        if ask and options and re.search(ask, instructions, re.I):
            fallbacks[ctype] = options[0]["clause_id"]
    return json.dumps({"variables": values, "fallbacks": fallbacks})


def register_fake_responders(fake: Any = None) -> None:
    fake = fake or fake_llm()
    fake.register("legal.review", review_responder)
    fake.register("legal.draft", draft_responder)


def register_compat_responder(fake: Any = None, agent: str = "legal_review") -> str:
    """For the /v1 compat proof: the gateway names that purpose 'compat.<agent>'. Opt-in, so other
    tests of the compat endpoint keep the default echo responder. Returns the prefix registered."""
    fake = fake or fake_llm()
    prefix = f"compat.{agent}"
    fake.register(prefix, review_responder)
    return prefix
