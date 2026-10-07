"""The follow-up e-mail draft: check the local model's JSON, or fall back to a code template.

The draft becomes the params of a crm.send_email proposal (tier 3), so a person reads every word
before anything is sent. The checks here keep obvious exfiltration shapes out of it anyway: no
links and no e-mail addresses in the text (the recipient is a CRM contact chosen by code, never an
address the model wrote), no markup, bounded length.
"""

from __future__ import annotations

import json
import re

MAX_SUBJECT = 200
MAX_BODY = 4000
_LINK_OR_ADDRESS = re.compile(r"(?i)\b(?:https?://|www\.)|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|<[a-z/!]")


def _clean(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or _LINK_OR_ADDRESS.search(text):
        return None
    return text


def parse_draft(text: str) -> tuple[str, str] | None:
    """(subject, body) from the model's JSON reply, or None when anything is off."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) - {"subject", "body"}:
        return None
    subject = _clean(data.get("subject"), MAX_SUBJECT)
    body = _clean(data.get("body"), MAX_BODY)
    if subject is None or body is None or "\n" in subject:
        return None
    return subject, body


def template(*, account: str, contact_name: str | None, next_step: str) -> tuple[str, str]:
    """A plain draft built by code from CRM names and the playbook's first next step."""
    greeting = f"Dear {contact_name}," if contact_name else "Hello,"
    body = (f"{greeting}\n\nthank you for your time with Kestrelbyte. As a next step I would like to suggest "
            f"{next_step}. Would one of the coming weeks suit you?\n\nKind regards")
    return f"Next step with FleetPilot for {account}"[:MAX_SUBJECT], body
