"""The legal_review app's run input, parsed by code before any node runs.

POST /api/runs carries one string. The UI sends a JSON object naming the mode:

    {"mode": "review", "contract_id": "C-0004"}                    the stored contract
    {"mode": "review", "contract_id": "C-0004", "text": "..."}     a counterparty markup of C-0004
    {"mode": "review", "upload_id": "upl_..."}                     an uploaded file (POST /api/legal/uploads)
    {"mode": "draft", "template_type": "MSA", "account_id": "A-0003", "variables": {...}, "instructions": "..."}
    {"mode": "due", "month": "2026-11"}                            month optional: next month
    {"mode": "send", "contract_id": "C-0004", "to": "x@y.example", "note": "..."}

Plain text (the generic run form, the Q&A habit) is mapped by fixed rules, never by a model: a
short question about expiry or notice -> due; a short text naming exactly one contract id -> review
of that stored contract; a longer text -> review of the pasted contract. Anything else is refused
with a message that says what to send.

Everything here only shapes the request. Who may do it is decided later: the router (may_start),
the MCP servers (tool rules, RLS) and the proposal policy (four-eyes).
"""

from __future__ import annotations

import json
import re
from typing import Any

MODES = ("review", "draft", "due", "send")
CONTRACT_ID = re.compile(r"^C-[0-9]{4}$")
ACCOUNT_ID = re.compile(r"^A-[0-9]{4}$")
UPLOAD_ID = re.compile(r"^upl_[0-9a-z]{8,40}$")
MONTH = re.compile(r"^[0-9]{4}-[0-9]{2}$")
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+$")
MAX_NOTE = 2000
MAX_INSTRUCTIONS = 2000
MIN_PASTED_CONTRACT = 200  # shorter plain text is a question, not a contract

_ANY_CONTRACT_ID = re.compile(r"\bC-[0-9]{4}\b")
_ANY_MONTH = re.compile(r"\b(20[0-9]{2}-(?:0[1-9]|1[0-2]))\b")
_DUE_WORDS = re.compile(r"到期|续约|通知期|expir|due\b|renewal|notice period|ablauf|kündigung", re.I)
_DRAFT_KEYS = {"mode", "template_type", "account_id", "variables", "instructions"}
_KEYS = {"review": {"mode", "contract_id", "text", "upload_id"}, "due": {"mode", "month"},
         "send": {"mode", "contract_id", "to", "note"}, "draft": _DRAFT_KEYS}

HELP = ("Send a contract id like C-0004, paste the contract text, or ask for the contracts due next month. "
        "The legal page also offers drafting and sending.")


class InputError(ValueError):
    """The input cannot be turned into a request; the message is fixed text that says what to do."""


def _plain(text: str) -> dict[str, Any]:
    short = len(text) < MIN_PASTED_CONTRACT
    if short and _DUE_WORDS.search(text):
        m = _ANY_MONTH.search(text)
        return {"mode": "due", "month": m.group(1) if m else None}
    ids = sorted(set(_ANY_CONTRACT_ID.findall(text)))
    if short and len(ids) == 1:
        return {"mode": "review", "contract_id": ids[0], "text": "", "upload_id": None}
    if short:
        raise InputError(HELP)
    return {"mode": "review", "contract_id": None, "text": text, "upload_id": None}


def _opt_str(obj: dict, key: str, pattern: re.Pattern[str], what: str) -> str | None:
    v = obj.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not pattern.fullmatch(v.strip()):
        raise InputError(f"{key} must look like {what}.")
    return v.strip()


def _text(obj: dict, key: str, limit: int) -> str:
    v = obj.get(key) or ""
    if not isinstance(v, str):
        raise InputError(f"{key} must be text.")
    if len(v) > limit:
        raise InputError(f"{key} is too long (at most {limit} characters).")
    return v


def _structured(obj: dict) -> dict[str, Any]:
    mode = obj.get("mode")
    if mode not in MODES:
        raise InputError("mode must be review, draft, due or send.")
    unknown = set(obj) - _KEYS[mode]
    if unknown:
        raise InputError(f"Unknown fields for {mode}: {', '.join(sorted(unknown))}.")
    if mode == "review":
        cid = _opt_str(obj, "contract_id", CONTRACT_ID, "C-0004")
        upload = _opt_str(obj, "upload_id", UPLOAD_ID, "an id returned by the upload")
        text = obj.get("text") or ""
        if not isinstance(text, str):
            raise InputError("text must be the contract text.")
        if upload and text:
            raise InputError("Give either the pasted text or an upload, not both.")
        if not (cid or upload or text.strip()):
            raise InputError("Give a contract id, paste the text or upload a file.")
        return {"mode": "review", "contract_id": cid, "text": text, "upload_id": upload}
    if mode == "due":
        return {"mode": "due", "month": _opt_str(obj, "month", MONTH, "2026-11")}
    if mode == "send":
        cid = _opt_str(obj, "contract_id", CONTRACT_ID, "C-0004")
        to = _opt_str(obj, "to", EMAIL, "name@company.example")
        if not cid or not to:
            raise InputError("Sending needs the contract id and the recipient's e-mail address.")
        return {"mode": "send", "contract_id": cid, "to": to, "note": _text(obj, "note", MAX_NOTE).strip()}
    template = obj.get("template_type")
    if not isinstance(template, str) or not template:
        raise InputError("Choose a template (MSA, order_form or NDA).")
    account = _opt_str(obj, "account_id", ACCOUNT_ID, "A-0003")
    if not account:
        raise InputError("A draft needs the customer's account id (A-0003).")
    variables = obj.get("variables") or {}
    if not isinstance(variables, dict):
        raise InputError("variables must be an object.")
    return {"mode": "draft", "template_type": template, "account_id": account, "variables": variables,
            "instructions": _text(obj, "instructions", MAX_INSTRUCTIONS)}


def parse(text: str) -> dict[str, Any]:
    """The request as a dict with a `mode`, or InputError. Pure; no I/O."""
    if not isinstance(text, str) or not text.strip():
        raise InputError(HELP)
    s = text.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            raise InputError("The request is not valid JSON; send a JSON object with a mode.") from None
        if not isinstance(obj, dict):
            raise InputError("Send a JSON object with a mode.")
        return _structured(obj)
    return _plain(s)
