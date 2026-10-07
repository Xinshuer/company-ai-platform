"""FakeLLM responders for the 'sales.' purposes: deterministic, schema-valid, using only the prompt.

The extractor reads the pages out of the user message and applies corpai.sales.heuristics, i.e. it
behaves like a careful model that ignores instructions inside pages. Tests that need a compromised
model register their own responder for the longer prefix 'sales.web_extract'.
"""

from __future__ import annotations

import json

from corpai.sales.heuristics import extract_signals


def _pages_from(messages: list[dict]) -> list[dict]:
    for m in reversed(messages):
        if m.get("role") != "user" or not isinstance(m.get("content"), str):
            continue
        try:
            data = json.loads(m["content"])
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("pages"), list):
            pages = [p for p in data["pages"] if isinstance(p, dict)]
            for p in pages:
                # The prompt carries no `kind`/`domain`: recover them from the URL like a reader would.
                url = str(p.get("url", ""))
                host = url.split("://", 1)[-1].split("/", 1)[0]
                p.setdefault("domain", host[4:] if host.startswith("www.") else host)
                p.setdefault("kind", "site" if host.startswith("www.") else "news")
            return pages
    return []


def web_extract(messages: list[dict], *, route: str, json_mode: bool) -> str:
    return json.dumps(extract_signals(_pages_from(messages)), ensure_ascii=False, sort_keys=True)


def sales_default(messages: list[dict], *, route: str, json_mode: bool) -> str:
    """Other sales purposes (e.g. a strategy note added by the wiring phase): a fixed, safe reply."""
    if json_mode:
        return json.dumps({"note": f"[fake/{route}] sales"}, sort_keys=True)
    return f"[fake/{route}] sales"


def register_fake_responders() -> None:
    from corpai.gateway import fake_llm

    fake = fake_llm()
    fake.register("sales.", sales_default)
    fake.register("sales.web_extract", web_extract)
