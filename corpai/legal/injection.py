"""Find text in a contract that is addressed to an AI system instead of to the other party.

Detection is code, not a model: a model that was taken over by the injection would also be the
one asked to report it. A hit becomes the finding "embedded_instruction" and the clause goes to a
human; it never changes anything else (the review has no write or send capability to misuse).

Matching runs on the original text and again on a normalised copy (format characters removed,
NFKC), so zero-width or fullwidth characters cannot hide "ignore previous instructions". Spans
are always offsets into the original text.

The patterns need an addressee or an imperative ("AI reviewer: ...", "Instructions for the AI
assistant", "Ignore the playbook", "rate the contract as low risk"), in English, German and
Chinese. Ordinary contract prose about AI is not a hit: ABC Tech sells AI routing software, so
"an AI-assisted routing service" or "the AI system processing driver data" (EU AI Act clauses)
appear in real contracts. A detector miss is backed by the code floor in review.py (never-accept
wording and numeric limits of the playbook keep a clause unacceptable whatever the model says).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Where an instruction to a reader starts: the beginning of a line, after sentence punctuation or an
# opening bracket/quote/dash, or after a salutation.
_LEAD = r"(?:^|[.:;!?\[(\"“‘'–—-]\s*|\b(?:dear|hello|hi|attention|attn\.?|please)\s+)"
_AI = r"(?:ai|a\.i\.|llm|gpt|chatbot|(?:large\s+)?language\s+model)"
_READER = r"(?:reviewers?|assistants?|agents?|models?|systems?|tools?|bots?)"
_DOC = r"(?:agreement|document|contract|text|clauses?|file|redline|draft)"
_RULES = r"(?:instructions?|prompts?|rules|guidance|guidelines|playbook|polic(?:y|ies)|directions)"
_STEER_TARGET = (r"(?:low[- ]risk|acceptable|accept(?:ed)?\s+with\s+low\s+risk|preferred(?:\s+(?:playbook\s+)?position)?"
                 r"|pre-?approved|within\s+(?:the\s+)?playbook)")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((kind, re.compile(p, re.I | re.M)) for kind, p in (
    ("override", r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+)?(the\s+|your\s+)?(previous|prior|above|"
                 r"earlier|preceding|other|existing)\s+" + _RULES),
    # imperative "Ignore the playbook ...", not "ABC Tech shall not disregard the Customer's instructions"
    ("override", _LEAD + r"(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+)?(?:the\s+|your\s+|its\s+)?"
                 r"(?:[\w-]+\s+){0,2}" + _RULES + r"\b"),
    # "Note to the AI reviewer", "Instructions for the AI assistant", "Note to automated reviewers"
    ("addressed_to_ai", r"\b(?:notes?|instructions?|message|guidance|directions?|reminder)\s+(?:for|to)\s+"
                        r"(?:the\s+|any\s+|all\s+)?(?:automated\s+|automatic\s+)?"
                        r"(?:" + _AI + r"|reviewers?|assistants?|models?|automated\s+systems?)\b"),
    # "AI reviewer: ...", "To any AI system: ..." (a colon right after the addressee)
    ("addressed_to_ai", _LEAD + r"(?:to\s+)?(?:the\s+|any\s+|all\s+)?(?:automated\s+)?" + _AI + r"\s*"
                        + _READER + r"?\s*:"),
    # "Dear AI assistant, ...", "AI reviewer, ..." (not "The AI system, as defined in Article 3, ...")
    ("addressed_to_ai", _LEAD + r"(?:" + _AI + r"\s+(?:reviewers?|assistants?|agents?)|automated\s+reviewers?)\s*,"),
    ("addressed_to_ai", r"\b(?:" + _AI + r"\s+reviewers?|automated\s+reviewers?)\b"),
    # "any AI system reviewing this agreement" (aimed at *this* document, not "the AI system processing data")
    ("addressed_to_ai", r"\b" + _AI + r"\s+" + _READER + r"\s+(?:that\s+(?:is|are)\s+)?(?:reviewing|processing|"
                        r"reading|summari[sz]ing|analy[sz]ing|checking|assessing)\s+(?:this|the\s+present)\s+" + _DOC),
    # "As an AI, you ..." (not "as an AI-assisted routing service")
    ("addressed_to_ai", r"\bas\s+an?\s+" + _AI + r"(?:\s+(?:model|system|assistant|reviewer))?\s*,?\s+"
                        r"(?:you|your|i|we)\b"),
    ("addressed_to_ai", r"\b(system\s+prompt|you\s+are\s+now|new\s+instructions\s*:)"),
    ("verdict_steering", r"\b(do\s+not|don't|never)\s+flag\b"),
    # "rate the contract as low risk", "mark it as acceptable" (not "classify each shipment as low-risk")
    ("verdict_steering", r"\b(?:rate|mark|classify|score|treat|assess|report|label)\s+(?:(?:this|the|these|that)\s+"
                         r"(?:contract|agreement|clauses?|document|section|text|position|terms?|liability\s+position)"
                         r"|it|them|everything|all\s+clauses)\b[^.\n]{0,40}?\b" + _STEER_TARGET),
    ("verdict_steering", r"\bpre-?approved\s+by\b[^.\n]{0,40}\b(?:head\s+of\s+legal|legal\s+(?:department|team)|"
                         r"general\s+counsel)"),
    # tool-call shaped requests: "call legal.send_contract", "invoke crm.send_email"
    ("tool_request", r"\b(call|invoke|run|execute|trigger)\s+(the\s+)?(tool\s+|function\s+)?`?[a-z]+\.[a-z]+_[a-z_]+"),
    # German: "Hinweis an die KI", "Ignoriere die vorherigen Anweisungen", "als unbedenklich einstufen"
    ("addressed_to_ai", r"\b(?:hinweise?|anweisung(?:en)?|nachricht|notiz)\s+(?:an|für)\s+"
                        r"(?:die\s+|den\s+|das\s+|alle\s+|jede[ns]?\s+)?(?:ki\b|k\.i\.|llm\b|sprachmodell|modell\b|"
                        r"assistent(?:en)?\b|automatisierte[nr]?\s+\w+|ki-\w+|prüfer)"),
    ("override", r"\b(?:ignorier(?:e|en|t)?|missachte(?:n)?|vergiss|vergessen\s+sie)\b[^.\n]{0,50}"
                 r"\b(?:anweisungen|regeln|vorgaben|instruktionen|richtlinien|playbook)"),
    ("verdict_steering", r"\bals\s+(?:akzeptabel|unbedenklich|geringes\s+risiko|niedriges\s+risiko)\s+"
                         r"(?:einstufen|bewerten|markieren|kennzeichnen)"),
    # Chinese (no \b: CJK characters count as word characters): "ignore ... instructions",
    # "a note to the AI", "AI reviewer:", "mark as acceptable / low risk".
    ("override", r"(?:忽略|无视|忽视|不要理会)[^。\n]{0,15}"
                 r"(?:指示|指令|规则|说明|提示|要求)"),
    ("addressed_to_ai", r"(?:致|给|对)\s*(?:所有|任何)?\s*(?:ai|人工智能|"
                        r"大模型|模型|助手)[^。\n]{0,6}?(?:的)?"
                        r"(?:说明|提示|指示|备注|留言|[:：])"),
    ("addressed_to_ai", r"(?:ai|人工智能|模型)\s*(?:审阅|审核|审查|"
                        r"助手|系统)(?:员|者)?\s*[:：]"),
    ("verdict_steering", r"(?:标记|评为|判定|归类)为?\s*"
                         r"(?:可接受|低风险|已批准)"),
))
MAX_QUOTE = 600
_HEADING = re.compile(r"(?:#{1,6}[ \t]+)?\d+(?:\.\d+)*\.?[ \t]+[A-Z][^\n]{0,80}$")


@dataclass(frozen=True)
class InstructionHit:
    start: int
    end: int
    text: str  # == contract_text[start:end]
    kinds: tuple[str, ...]


def _normalised(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch >= "\x80":
            if unicodedata.category(ch) == "Cf":
                continue
            ch = unicodedata.normalize("NFKC", ch)
        for c in ch:
            chars.append(c)
            index.append(i)
    index.append(len(text))
    return "".join(chars), index


def _paragraph(text: str, start: int, end: int) -> tuple[int, int]:
    """The blank-line-delimited paragraph around a match, trimmed; long paragraphs are cut to a
    window around the match so the quote stays readable."""
    p_start = text.rfind("\n\n", 0, start)
    p_start = 0 if p_start < 0 else p_start + 2
    p_end = text.find("\n\n", end)
    p_end = len(text) if p_end < 0 else p_end
    while p_start < p_end and text[p_start].isspace():
        p_start += 1
    # A clause heading on the paragraph's first line is not part of the injected text.
    first_nl = text.find("\n", p_start, start)
    if first_nl >= 0 and _HEADING.match(text, p_start, first_nl):
        p_start = first_nl + 1
    while p_end > p_start and text[p_end - 1].isspace():
        p_end -= 1
    if p_end - p_start > MAX_QUOTE:
        w_start = max(p_start, start - MAX_QUOTE // 3)
        return w_start, min(p_end, max(end, w_start + MAX_QUOTE))
    return p_start, p_end


def find_embedded_instructions(text: str) -> list[InstructionHit]:
    if not isinstance(text, str) or not text:
        return []
    raw: list[tuple[int, int, str]] = []
    for kind, pat in _PATTERNS:
        raw.extend((m.start(), m.end(), kind) for m in pat.finditer(text))
    norm, index = _normalised(text)
    if norm != text:
        for kind, pat in _PATTERNS:
            raw.extend((index[m.start()], index[m.end() - 1] + 1, kind) for m in pat.finditer(norm))
    if not raw:
        return []
    # One hit per paragraph, with every pattern kind that fired inside it.
    by_para: dict[tuple[int, int], set[str]] = {}
    for s, e, kind in raw:
        by_para.setdefault(_paragraph(text, s, e), set()).add(kind)
    hits = [InstructionHit(s, e, text[s:e], tuple(sorted(kinds))) for (s, e), kinds in by_para.items()]
    hits.sort(key=lambda h: h.start)
    merged: list[InstructionHit] = []
    for h in hits:
        if merged and h.start < merged[-1].end:
            last = merged[-1]
            end = max(last.end, h.end)
            merged[-1] = InstructionHit(last.start, end, text[last.start:end],
                                        tuple(sorted(set(last.kinds) | set(h.kinds))))
        else:
            merged.append(h)
    return merged
