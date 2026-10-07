"""Protected attributes (GDPR Art. 9, AGG §1) and health data.

Two consumers:
- redact_protected(): HR résumé screening (decision D6). Before a résumé reaches a model we remove
  photo references, gender, age and date of birth, ethnicity, nationality and place of origin,
  marital status and children, religion, health and disability. It errs on the side of removing
  too much: a removed skill costs little, a leaked attribute biases the screening.
- classify_text(): the narrower subset marked `in_classify` (field values and unambiguous phrases
  about an individual). A question *about* health data or nationality rules ("What does GDPR say
  about health data?") must stay L1, so bare topic words do not count there.
"""

from __future__ import annotations

import re

from corpai.types import Level

from .model import Finding
from .normalise import spans_with_hidden

# ---- field labels: "Label: value" ----------------------------------------------------------

_LABELS: dict[str, str] = {
    "photo": r"photo|photograph|picture|headshot|foto|bewerbungsfoto|lichtbild|照片|头像|证件照",
    "gender": r"gender|sex|geschlecht|anrede|性别",
    "date_of_birth": r"date of birth|d\.?o\.?b\.?|birth ?date|birthday|born|geburtsdatum|geboren|geburtstag"
                     r"|出生日期|出生年月|生日",
    "age": r"age|alter|年龄",
    "ethnicity": r"ethnicity|ethnic origin|ethnic background|race|ethnie|ethnische herkunft|民族",
    "nationality": r"nationality|nationalities|citizenship|staatsangehörigkeit|staatsbürgerschaft|nationalität|国籍",
    "origin": r"place of birth|birthplace|hometown|home town|country of origin|geburtsort|herkunftsland|herkunft"
              r"|籍贯|出生地|户籍|户口所在地",
    "marital_status": r"marital status|civil status|relationship status|familienstand|婚姻状况|婚否|婚育状况|婚育",
    "children": r"number of children|children|kids|anzahl der kinder|kinder|子女|生育状况",
    "religion": r"religious affiliation|religion|denomination|konfession|religionszugehörigkeit|宗教信仰|宗教",
    "health": r"health status|health|medical condition|gesundheitszustand|gesundheit|健康状况|身体状况",
    "disability": r"disabilities|disability|grad der behinderung|schwerbehinderung|behinderung|残疾",
}
_LABEL_TO_KIND: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?i)^(?:{alts})$"), kind) for kind, alts in _LABELS.items()
]
_ANY_LABEL = "|".join(alts for alts in _LABELS.values())
# Fields are found from their colon backwards: trying a case-insensitive alternation of ~80
# labels at every position of the text costs more than the rest of the classifier together.
_COLON = re.compile(r"[:：]")
_LABEL_BEFORE = re.compile(rf"(?i)(?<![\w])(?:{_ANY_LABEL})[ \t]{{0,3}}\Z")
# The value ends at a line end, a column separator, two spaces (tabular CVs) or the next label.
_VALUE = re.compile(
    rf"(?i)[ \t\u3000]{{0,4}}"
    rf"((?:(?![ \t\u3000]{{2,}}|[ \t]*[|;·•；]|[ \t]*(?:{_ANY_LABEL})[ \t]*[:：])[^\n]){{0,120}})"
)
_LABEL_WINDOW = 40


def _fields(text: str) -> list[tuple[int, int, str, str]]:
    """(start, end, label, value) of every "Label: value" field."""
    out: list[tuple[int, int, str, str]] = []
    for c in _COLON.finditer(text):
        i = c.start()
        m = _LABEL_BEFORE.search(text, max(0, i - _LABEL_WINDOW), i)
        if m is None:
            continue
        v = _VALUE.match(text, i + 1)
        if v is None:
            continue
        out.append((m.start(), v.end(1), m.group().strip(), v.group(1)))
    return out


# ---- inline phrases -------------------------------------------------------------------------

_I = re.IGNORECASE
# (kind, pattern, also used by classify_text)
_INLINE: list[tuple[str, re.Pattern[str], bool]] = [
    ("photo", re.compile(r"!\[[^\]\n]{0,200}\]\([^)\n]{0,500}\)|<img\b[^>]{0,500}>"), False),
    ("photo", re.compile(r"\[(?:photo|foto|picture|image|bild|lichtbild|照片)[^\]\n]{0,80}\]", _I), False),
    ("photo", re.compile(r"\b(?:photo|foto|lichtbild)\s+(?:attached|enclosed|anbei|beigefügt)\b"
                         r"|\b(?:with|mit)\s+(?:photo|foto)\b|附(?:照片|证件照)", _I), False),
    ("date_of_birth", re.compile(
        r"\b(?:born|geboren)[ \t]+(?:on[ \t]+|am[ \t]+|in[ \t]+)?(?:[^\n,;.]{0,30}?\b)?(?:19|20)\d{2}\b"
        # place names stay on the line: crossing it would swallow the next CV field
        r"(?:[ \t]+(?:in|in der|im)[ \t]+[A-ZÄÖÜ][\w-]{1,40}(?:,[ \t]*[A-ZÄÖÜ][\w-]{1,40})?)?", _I), True),
    ("date_of_birth", re.compile(r"出生于[^\s，。,;；]{1,20}|(?:19|20)\d{2}年(?:\d{1,2}月)?(?:\d{1,2}日)?出生"), True),
    ("age", re.compile(r"\b\d{2}\s*(?:years old|year-old|-year-old|yrs old|y/o|jahre alt)\b"
                       r"|\b\d{2}-jährige?[rnms]?\b|\baged\s+\d{2}\b", _I), True),
    ("age", re.compile(r"(?<!\d)\d{2}\s*周?岁"), True),
    # the lead phrase in any case, the place name capitalised
    ("origin", re.compile(r"\b(?i:born in|originally from|native of|grew up in|hails from|stammt aus|"
                          r"aufgewachsen in|gebürtig(?:e[rs]?)? aus|geboren in)[ \t]+[A-ZÄÖÜ][\w-]{1,40}"
                          r"(?:,?[ \t]+[A-ZÄÖÜ][\w-]{1,40})?"), False),
    ("origin", re.compile(r"来自[\u4e00-\u9fff]{2,6}"), False),
    ("nationality", re.compile(r"\b[A-Z][a-zäöü]{2,20}\s+(?:citizen|citizenship|national|passport holder)\b"
                               r"|\b(?:[A-Z][a-zäöü]{2,20}e[rn]?\s+)?Staatsbürger(?:in)?\b"), False),
    ("ethnicity", re.compile(r"(?:汉|回|满|维吾尔|藏|蒙古|壮|苗|彝|土家|朝鲜|侗|瑶|白|哈萨克|傣|黎)族"), True),
    # Bare status words also appear in policy questions ("parental leave policy"): redact only.
    ("marital_status", re.compile(r"\b(?:married|unmarried|divorced|widowed|verheiratet|ledig|geschieden|"
                                  r"verwitwet|verpartnert|in a civil partnership)\b", _I), False),
    ("marital_status", re.compile(r"已婚已育|已婚未育|已婚|未婚|离异|丧偶|已育|未育"), False),
    ("children", re.compile(r"\b(?:\d{1,2}|one|two|three|four|five|ein|eine|zwei|drei|vier)\s+"
                            r"(?:children|child|kids|sons?|daughters?|kinder|kind|söhne|sohn|töchter|tochter)\b"
                            r"|\b(?:mother|father|mum|dad|mutter|vater) (?:of|von)\s+\w+", _I), True),
    ("children", re.compile(r"\b(?:maternity leave|parental leave|elternzeit|mutterschutz)\b", _I), False),
    ("children", re.compile(r"\d{1,2}\s*个(?:孩子|子女)|育有\S{0,6}|一儿一女"), True),
    ("religion", re.compile(r"\b(?:catholic|protestant|muslim|jewish|hindu|buddhist|atheist|evangelical|"
                            r"römisch-katholisch|katholisch|evangelisch|muslimisch|jüdisch|"
                            r"church member|kirchenmitglied)\b", _I), False),
    # "Christian" is also a first name; only the adjective (not followed by a surname) is removed.
    ("religion", re.compile(r"\bChristian\b(?!\s+[A-Z])"), False),
    ("religion", re.compile(r"基督教|天主教|伊斯兰教|佛教徒|信仰[\u4e00-\u9fff]{1,6}教"), False),
    # Statements about one person's health count for classify_text ...
    ("health", re.compile(r"\b(?:diagnosed with|suffer(?:s|ed|ing)? from|leidet an|erkrankt an)\s+[^\n.,;]{1,40}"
                          r"|\b(?:(?:is|was|on|currently) sick leave|sick note|medical certificate|"
                          r"(?:is|was) pregnant|krankgeschrieben|krankschreibung|arbeitsunfähig\w*|"
                          r"ist schwanger)\b", _I), True),
    ("health", re.compile(r"患有\S{1,10}|确诊|请病假|怀孕了|病史"), True),
    ("disability", re.compile(r"\b(?:schwerbehindert(?:e[rn]?)?|wheelchair user|GdB\s*\d{1,3})\b", _I), True),
    # ... bare medical words are topics as often as facts ("health data under GDPR"): redact only.
    ("health", re.compile(r"\b(?:sick leave|chronic (?:illness|disease|condition)|pregnan(?:t|cy)|"
                          r"chronische (?:erkrankung|krankheit)|schwanger(?:schaft)?|burn-?out|depression|"
                          r"diabetes|cancer|krebs|illness|krankheit|erkrankung)\b", _I), False),
    ("health", re.compile(r"病假|怀孕|孕期|身体健康|健康状况良好"), False),
    ("disability", re.compile(r"\b(?:disabled|disability|handicapped|schwerbehinderung|behinderung|"
                              r"grad der behinderung)\b", _I), False),
    ("disability", re.compile(r"残疾\S{0,6}"), False),
    ("gender", re.compile(r"\b(?:male|female|männlich|weiblich|non-binary)\b", _I), False),
    ("gender", re.compile(r"(?<![A-Za-z])(?:Mr|Mrs|Ms|Miss|Mx|Herr|Frau)\.?(?=\s)"), False),
    # English pronouns reveal gender; German er/sie are skipped (sie is also the formal "you").
    ("gender", re.compile(r"\b(?:he|she|him|his|her|hers|himself|herself)\b", _I), False),
    ("gender", re.compile(r"男性|女性|(?<=[\u4e00-\u9fff])(?:先生|女士|小姐)"), False),
]

# Field labels that classify_text counts. A photo label says nothing about a person by itself;
# redact_protected removes every label.
_CLASSIFY_FIELD_KINDS = frozenset(_LABELS) - {"photo"}
_HEALTH_KINDS = frozenset({"health", "disability"})


_CONFIG_VALUES = frozenset({"true", "false", "null", "none", "ok", "n/a", "-", "yes", "no", "0", "1"})


def _looks_like_config(value: str) -> bool:
    # YAML/JSON keys such as `children: []` or `health: ok` are code, not facts about a person.
    v = value.strip().rstrip(",").strip("\"'").lower()
    return not v or v[0] in "[{(<$" or v in _CONFIG_VALUES


def _field_kind(label: str) -> str:
    label = " ".join(label.split())
    for pat, kind in _LABEL_TO_KIND:
        if pat.match(label):
            return kind
    return "protected_attr"


def _spans(text: str, *, for_classify: bool) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for start, end, label, value in _fields(text):
        if not value.strip():
            continue  # a heading like "Health:" with nothing after it is not an attribute value
        kind = _field_kind(label)
        if for_classify and (kind not in _CLASSIFY_FIELD_KINDS or _looks_like_config(value)):
            continue
        spans.append((start, end, kind))
    for kind, pat, in_classify in _INLINE:
        if for_classify and not in_classify:
            continue
        spans.extend((m.start(), m.end(), kind) for m in pat.finditer(text))
    return spans


def _resolve(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Merge overlaps; the earliest, longest span names the kind."""
    spans.sort(key=lambda s: (s[0], -s[1]))
    out: list[tuple[int, int, str]] = []
    for s, e, k in spans:
        if e <= s:
            continue
        if out and s < out[-1][1]:
            ps, pe, pk = out[-1]
            out[-1] = (ps, max(pe, e), pk)
        else:
            out.append((s, e, k))
    return out


def classify_spans(text: str) -> list[Finding]:
    """L3 findings for classify_text: kind "health" for health/disability, else "protected_attr"."""
    return [Finding("health" if k in _HEALTH_KINDS else "protected_attr", s, e, Level.L3)
            for s, e, k in _resolve(_spans(text, for_classify=True))]


def redact_protected(text: str) -> tuple[str, list[Finding]]:
    """Remove protected attributes from a résumé; each span becomes ⟨removed:<kind>⟩.

    Finding offsets refer to the *input* text. Kinds: photo, gender, age, date_of_birth, ethnicity,
    nationality, origin, marital_status, children, religion, health, disability.
    """
    # Résumés are untrusted: "A<ZWSP>ge: 34" or "Ａｇｅ: 34" reads as "Age: 34" to the screening
    # model, so fields hidden that way are found in normalised form and removed from the raw text.
    spans = _resolve(spans_with_hidden(text, lambda t: _spans(t, for_classify=False)))
    parts: list[str] = []
    pos = 0
    for s, e, k in spans:
        parts.append(text[pos:s])
        parts.append(f"⟨removed:{k}⟩")
        pos = e
    parts.append(text[pos:])
    return "".join(parts), [Finding(k, s, e, Level.L3) for s, e, k in spans]
