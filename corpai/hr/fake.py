"""FakeLLM responders for 'hr.' purposes: deterministic, schema-valid, using only the prompt text.

The screening responder behaves like a cautious reader: it quotes the résumé line that best covers
the requirement's keywords, compares "at least N years" with the stated years of experience, and
says unknown when nothing matches. It never reads the requisition's metadata or the candidate's
identity (it has neither), so identical prompts give identical verdicts.
"""

from __future__ import annotations

import json
import re

_STOP = frozenset("""a an and or the of in on for with to at least years year experience professional
strong hands-on hands advanced written spoken degree eligible work production similar other problems""".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z+#-]*")
_REQ_YEARS = re.compile(r"\bat least (\d{1,2}) years?\b", re.IGNORECASE)
_CV_YEARS = re.compile(r"\b(\d{1,2}) years? of professional experience\b", re.IGNORECASE)


def _payload(messages: list[dict]) -> tuple[str, str]:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            try:
                data = json.loads(m["content"])
            except ValueError:
                continue
            if isinstance(data, dict):
                return str(data.get("requirement") or ""), str(data.get("resume") or "")
    return "", ""


def _keywords(requirement: str) -> list[str]:
    words = [w.strip("-").lower() for w in _WORD.findall(requirement)]
    return [w for w in dict.fromkeys(words) if len(w) >= 2 and w not in _STOP]


def _best_line(resume: str, keywords: list[str]) -> tuple[str | None, int]:
    best, score = None, 0
    for line in resume.splitlines():
        text = line.strip().lstrip("-").strip()
        if not text or text.startswith("#"):
            continue
        tokens = {w.lower() for w in _WORD.findall(text)}
        hits = sum(1 for k in keywords if k in tokens)
        if hits > score:
            best, score = text, hits
    return best, score


def screen(messages: list[dict], *, route: str, json_mode: bool) -> str:
    requirement, resume = _payload(messages)
    keywords = _keywords(requirement)
    need = _REQ_YEARS.search(requirement)
    have = _CV_YEARS.search(resume)
    line, hits = _best_line(resume, keywords)
    if need and have:
        years_line = next(l.strip() for l in resume.splitlines() if have.group(0) in l)
        if int(have.group(1)) < int(need.group(1)):
            out = {"verdict": "not_met", "quote": years_line}
        elif hits:
            out = {"verdict": "met", "quote": line}
        else:
            out = {"verdict": "unknown", "quote": None}
    elif hits:
        out = {"verdict": "met", "quote": line}
    else:
        out = {"verdict": "unknown", "quote": None}
    return json.dumps(out, ensure_ascii=False)


def hr_default(messages: list[dict], *, route: str, json_mode: bool) -> str:
    if json_mode:
        return json.dumps({"note": f"[fake/{route}] hr"})
    return f"[fake/{route}] hr"


def register_fake_responders() -> None:
    from corpai.gateway import fake_llm

    fake = fake_llm()
    fake.register("hr.", hr_default)
    fake.register("hr.screen", screen)
