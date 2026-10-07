"""draft_contract: a first draft assembled by code from reviewed clause-library wording.

The model does not write contract text (design-review 三 法务). It may only
  1. fill the template variables that the caller left open, and only those (any other key, a
     wrong type or a value that could carry structure rejects its whole answer), and
  2. pick a *fallback* clause from legal.clause_library for a negotiable clause type when the
     lawyer's instructions ask for one ("unacceptable" wording is never offered).
Everything else (template, section order, library wording, substitution) is code. Values the
caller passed always win over the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from corpai.classify import classify_text
from corpai.gateway import ChatRequest, get_gateway
from corpai.legal.library import clause_library, library_choice, require_start
from corpai.legal.playbook import load_playbook
from corpai.legal.results import DraftClause, DraftResult
from corpai.types import Level, Principal

PURPOSE = "legal.draft"
AGENT = "legal_review"
MAX_VALUE_CHARS = 120
_BAD_VALUE = re.compile(r"[\x00-\x1f\x7f{}\[\]<>#`|]")  # no line breaks, markup or template syntax


@dataclass(frozen=True)
class Variable:
    name: str
    kind: str  # text | int | date
    description: str
    minimum: int = 1
    maximum: int = 100_000


VARIABLES: dict[str, Variable] = {v.name: v for v in (
    Variable("customer_name", "text", "legal name of the customer"),
    Variable("customer_city", "text", "city of the customer's registered seat"),
    Variable("fleet_size", "int", "number of vehicles covered", 1, 100_000),
    Variable("effective_on", "date", "start date, YYYY-MM-DD"),
    Variable("term_months", "int", "initial term in months", 1, 60),
)}

# A section is (heading, fixed text with {variables} or None, clause_type from the library or None).
Section = tuple[str, str | None, str | None]


@dataclass(frozen=True)
class Template:
    title: str
    variables: tuple[str, ...]
    sections: tuple[Section, ...]

    @property
    def clause_types(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(ct for _, _, ct in self.sections if ct))


_PARTIES = ('Between Kestrelbyte GmbH, Berlin, Germany ("Kestrelbyte") and {customer_name}, {customer_city} '
            '("Customer").')
TEMPLATES: dict[str, Template] = {
    "MSA": Template("Master Subscription Agreement",
                    ("customer_name", "customer_city", "fleet_size", "effective_on", "term_months"), (
        ("Definitions", "Capitalised terms have the meaning given in Schedule 1.", None),
        ("Services", "Kestrelbyte provides FleetPilot as software-as-a-service for the Customer's fleet of about "
                     "{fleet_size} vehicles, including driver app, dispatch board and API access.", None),
        ("Term and renewal", "This agreement runs from {effective_on} for an initial term of {term_months} months.",
         "auto_renewal"),
        ("Service levels", "Monthly availability target 99.5 %.", "service_credits"),
        ("Liability", None, "liability_cap"),
        ("Data protection", None, "data_processing"),
        ("Personal data breaches", None, "breach_notification"),
        ("Confidentiality", "Both parties keep confidential information secret during the term and for three years "
                            "after.", None),
        ("Governing law and venue", None, "governing_law"),
    )),
    "order_form": Template("Order Form",
                           ("customer_name", "customer_city", "fleet_size", "effective_on", "term_months"), (
        ("Order", "FleetPilot Professional for {fleet_size} vehicles, billed quarterly in advance, under the "
                  "Kestrelbyte terms of service.", None),
        ("Term and renewal", "This order runs from {effective_on} for {term_months} months.", "auto_renewal"),
        ("Liability", None, "liability_cap"),
        ("Data protection", None, "data_processing"),
        ("Governing law and venue", None, "governing_law"),
    )),
    "NDA": Template("Mutual Non-Disclosure Agreement",
                    ("customer_name", "customer_city", "effective_on", "term_months"), (
        ("Purpose", "The parties exchange confidential information to evaluate a FleetPilot deployment.", None),
        ("Confidentiality", "Each party keeps the other's confidential information secret, uses it only for the "
                            "purpose and protects it with at least reasonable care. Obligations survive three "
                            "years after expiry.", None),
        ("Term", "This agreement runs from {effective_on} for {term_months} months.", None),
        ("Governing law", None, "governing_law"),
    )),
}

_SYSTEM = (
    "You help Kestrelbyte's legal team prepare a contract draft from an approved template. You never write "
    "contract text. Return only a JSON object {\"variables\": {...}, \"fallbacks\": {...}}. In \"variables\" "
    "give values only for the names listed under \"fill\", only when the lawyer's instructions state them, in "
    "the listed type; leave a variable out if you cannot tell. In \"fallbacks\" map a clause type to one of "
    "the offered fallback clause ids only when the instructions ask for that concession; otherwise leave it "
    "empty.")


class DraftRejected(ValueError):
    """The caller passed variables the template does not have, or invalid values."""


def _coerce(var: Variable, value: Any) -> Any:
    """The value in its canonical type, or ValueError. Shared by caller and model input."""
    if var.kind == "int":
        if isinstance(value, bool):
            raise ValueError(var.name)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if not isinstance(value, int) or not var.minimum <= value <= var.maximum:
            raise ValueError(var.name)
        return value
    if var.kind == "date":
        if isinstance(value, date):
            return value.isoformat()
        if not isinstance(value, str):
            raise ValueError(var.name)
        return date.fromisoformat(value.strip()).isoformat()
    if not isinstance(value, str):
        raise ValueError(var.name)
    value = value.strip()
    if not value or len(value) > MAX_VALUE_CHARS or _BAD_VALUE.search(value):
        raise ValueError(var.name)
    return value


def _validate_model_answer(answer: Any, fill: list[str], fallback_options: dict[str, list[dict]]
                           ) -> tuple[dict[str, Any], dict[str, str]]:
    """(variables, fallbacks) from the model, or ValueError with a short reason. All or nothing:
    one unexpected key means the answer is not trusted at all."""
    if not isinstance(answer, dict) or set(answer) - {"variables", "fallbacks"}:
        raise ValueError("unexpected top-level keys")
    variables = answer.get("variables") or {}
    fallbacks = answer.get("fallbacks") or {}
    if not isinstance(variables, dict) or not isinstance(fallbacks, dict):
        raise ValueError("variables and fallbacks must be objects")
    extra = set(variables) - set(fill)
    if extra:
        raise ValueError(f"model returned variables that were not asked for: {sorted(extra)}")
    clean = {}
    for name, value in variables.items():
        try:
            clean[name] = _coerce(VARIABLES[name], value)
        except (ValueError, TypeError):
            raise ValueError(f"invalid value for {name}") from None
    chosen = {}
    for ctype, cid in fallbacks.items():
        # A list or object here would make the set membership test raise TypeError (unhashable),
        # an unhandled crash instead of a rejected answer.
        if not isinstance(ctype, str) or not isinstance(cid, str):
            raise ValueError("fallbacks must map a clause type to one clause id")
        allowed = {o["clause_id"] for o in fallback_options.get(ctype, [])}
        if cid not in allowed:
            raise ValueError(f"fallback {cid!r} is not an allowed fallback for {ctype!r}")
        chosen[ctype] = cid
    return clean, chosen


def _render(template: Template, values: dict[str, Any], picks: dict[str, dict], missing: list[str]) -> str:
    def sub(s: str) -> str:
        # Placeholders are replaced one by one (no str.format: a value can never introduce a placeholder).
        for name in template.variables:
            s = s.replace("{" + name + "}", str(values[name]) if name in values else f"[[{name}]]")
        return s

    lines = [f"{template.title} (draft)", "", sub(_PARTIES), ""]
    for n, (heading, fixed, ctype) in enumerate(template.sections, start=1):
        body = [sub(fixed)] if fixed else []
        if ctype:
            pick = picks.get(ctype)
            body.append(pick["text"] if pick else f"[[clause library has no wording for {ctype}]]")
        lines += [f"## {n}. {heading}", " ".join(body), ""]
    if missing:
        lines.append("Open points: " + ", ".join(missing) + ".")
    return "\n".join(lines).rstrip() + "\n"


async def draft_contract(principal: Principal, *, template_type: str, variables: dict, run_id: str,
                         instructions: str = "", gateway: Any = None) -> DraftResult:
    """Draft a contract from TEMPLATES[template_type] and the clause library.

    `variables`: known values (must be template variables, else DraftRejected). `instructions`: the
    lawyer's notes; the model reads them to fill open variables and to pick allowed fallbacks.
    """
    if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
        raise ValueError("run_id must be a non-empty string")
    await require_start(principal, run_id)
    template = TEMPLATES.get(template_type)
    if template is None:
        raise DraftRejected(f"unknown template; use one of {sorted(TEMPLATES)}")
    if not isinstance(variables, dict):
        raise DraftRejected("variables must be an object")
    extra = set(variables) - set(template.variables)
    if extra:
        raise DraftRejected(f"unknown variables for {template_type}: {sorted(extra)}")
    if not isinstance(instructions, str) or len(instructions) > 4000:
        raise DraftRejected("instructions must be text of at most 4000 characters")
    values: dict[str, Any] = {}
    for name, value in variables.items():
        try:
            values[name] = _coerce(VARIABLES[name], value)
        except (ValueError, TypeError):
            raise DraftRejected(f"invalid value for {name}") from None

    playbook = load_playbook()
    library = await clause_library(principal, run_id)
    picks = {ct: row for ct in template.clause_types if (row := library_choice(library, ct, "preferred"))}
    fallback_options = {ct: [{"clause_id": r["clause_id"], "text": r["text"]} for r in library
                             if r["clause_type"] == ct and r["position"] == "fallback"]
                        for ct in template.clause_types}
    fallback_options = {k: v for k, v in fallback_options.items() if v}
    fill = [n for n in template.variables if n not in values]
    model_called = False
    rejected: str | None = None
    filled: list[str] = []
    chosen: dict[str, str] = {}
    hwm = Level(max(int(Level.L3), int(playbook.level), *(int(r["level"]) for r in library),
                    int(classify_text(instructions + " " + json.dumps(values, ensure_ascii=False)).level)))
    if instructions.strip() and (fill or fallback_options):
        payload = {
            "template_type": template_type,
            "fill": [{"name": n, "type": VARIABLES[n].kind, "description": VARIABLES[n].description} for n in fill],
            "known": values, "instructions": instructions, "fallback_options": fallback_options,
        }
        req = ChatRequest(messages=[{"role": "system", "content": _SYSTEM},
                                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                          principal=principal, hwm=hwm, purpose=PURPOSE, run_id=run_id, agent=AGENT,
                          json_mode=True, max_tokens=600, temperature=0.0)
        result = await (gateway or get_gateway()).chat(req)
        model_called = True
        try:
            answer = json.loads(result.text)
            model_values, chosen = _validate_model_answer(answer, fill, fallback_options)
        except json.JSONDecodeError:
            rejected, model_values, chosen = "the answer was not JSON", {}, {}
        except ValueError as e:
            rejected, model_values, chosen = str(e), {}, {}
        for name, value in model_values.items():
            values[name] = value
            filled.append(name)
        hwm = Level(max(int(hwm), int(result.level)))
    for ctype, cid in chosen.items():
        picks[ctype] = next(r for r in library if r["clause_id"] == cid)
    missing = [n for n in template.variables if n not in values]
    clauses = [DraftClause(section=h, clause_type=ct, clause_id=picks[ct]["clause_id"] if ct in picks else None,
                           position=picks[ct]["position"] if ct in picks else "fixed") if ct else
               DraftClause(section=h, clause_type=None, clause_id=None, position="fixed")
               for h, _, ct in template.sections]
    return DraftResult(
        run_id=run_id, template_type=template_type, title=template.title,
        text=_render(template, values, picks, missing), variables=values,
        provided=sorted(variables), filled_by_model=filled, missing=missing, clauses=clauses, fallbacks=chosen,
        playbook_ref=playbook.ref, level=int(hwm), model_called=model_called, model_output_rejected=rejected)
