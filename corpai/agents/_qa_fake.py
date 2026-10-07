"""Deterministic FakeLLM responders for the Q&A graph (purposes "qa.tools" and "qa.answer").

They behave like a well-mannered model: the tool chooser picks tools by keywords in the question
and only among the tools listed as available; the answerer quotes the first sources and cites them
with [doc:<id>] markers. Security tests register misbehaving responders on top of these.
"""

from __future__ import annotations

import json
import re
from typing import Any

AVAILABLE_PREFIX = "Available tools:"

# (keywords, tool name, arguments); a rule fires when any keyword is in the lower-cased question
_RULES: list[tuple[tuple[str, ...], str, dict[str, Any]]] = [
    (("account", "customer", "客户"), "crm__search_accounts", {"query": ""}),
    (("project", "项目"), "code__get_project_status", {}),
    (("regulation", "gdpr", "ai act", "法规"), "legal__search_regulations", {"query": ""}),
]
_EXPIRING = ("expiring", "expire", "renewal", "到期")
_CONTRACT = ("contract", "合同")
_REG_TERMS = ("GDPR", "AI Act")

_DOC_LINE = re.compile(r"^\[doc:([^\]\s]{1,200})\] (.*)$", re.M)
_TOOL_LINE = re.compile(r"^\[tool:([a-z]+\.[a-z_]+)\] sources: (.*)$", re.M)


def _text(m: Any) -> str:
    c = m.get("content") if isinstance(m, dict) else None
    if isinstance(c, list):
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c if isinstance(c, str) else ""


def _offered(messages: list[dict]) -> set[str]:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            for line in _text(m).splitlines():
                if line.startswith(AVAILABLE_PREFIX):
                    return {t.strip() for t in line[len(AVAILABLE_PREFIX):].split(",") if t.strip()}
    return set()


def _question(messages: list[dict]) -> str:
    users = [_text(m) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    return users[-1] if users else ""


def tools_responder(messages: list[dict], *, route: str, json_mode: bool) -> dict:
    if any(isinstance(m, dict) and m.get("role") == "tool" for m in messages):
        return {"content": "", "tool_calls": []}  # one round is enough for the fake
    offered = _offered(messages)
    q = _question(messages)
    low = q.lower()
    chosen: list[tuple[str, dict]] = []
    for words, name, args in _RULES:
        if any(w in low for w in words):
            if name == "legal__search_regulations":
                args = {"query": next((t for t in _REG_TERMS if t.lower() in low), "")}
            chosen.append((name, args))
    if any(w in low for w in _EXPIRING) and any(w in low for w in _CONTRACT):
        chosen.append(("legal__contracts_expiring", {"within_days": 60}))
    calls = [{"id": f"call_{i}", "type": "function",
              "function": {"name": name, "arguments": json.dumps(args, sort_keys=True)}}
             for i, (name, args) in enumerate(chosen, start=1) if name in offered]
    return {"content": "", "tool_calls": calls}


def answer_responder(messages: list[dict], *, route: str, json_mode: bool) -> str:
    ctx = "\n".join(_text(m) for m in messages if isinstance(m, dict))
    lines: list[str] = []
    lines_after = ctx.splitlines()
    for m in _DOC_LINE.finditer(ctx):
        if len(lines) >= 3:
            break
        doc_id, title = m.group(1), m.group(2).split(" — ")[0]
        # the first non-empty line after the marker is the chunk text
        idx = ctx[:m.end()].count("\n")
        snippet = next((s.strip() for s in lines_after[idx + 1:idx + 8]
                        if s.strip() and not s.lstrip().startswith("#")), "")
        lines.append(f"- {title}: {snippet[:140]} [doc:{doc_id}]")
    for m in _TOOL_LINE.finditer(ctx):
        sources = [s.strip() for s in m.group(2).split(",") if s.strip() and s.strip() != "-"]
        cites = " ".join(f"[doc:{s}]" for s in sources[:2])
        lines.append(f"- {m.group(1)} returned {len(sources)} record(s) {cites}".rstrip())
    if not lines:
        return f"[fake/{route}] I found nothing you may read about this."
    return f"[fake/{route}] Based on the sources you may read:\n" + "\n".join(lines)


def install(fake: Any | None = None) -> None:
    """Register the responders unless a test already put its own on these purposes."""
    from corpai.gateway import default_responder, fake_llm

    fake = fake or fake_llm()
    for purpose, fn in (("qa.tools", tools_responder), ("qa.answer", answer_responder)):
        if fake.responder_for(purpose) is default_responder:
            fake.register(purpose, fn)
