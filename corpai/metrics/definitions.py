"""Metric definitions from config/metrics.yaml, validated at load (design-review 二.8, D7).

The loader is the guard rail that keeps the metric layer aggregate-only: it refuses person-level
dimensions, people-derived metrics without a minimum group size, pay and performance sources, SQL that
reaches outside the business schemas, and a level below the sources' default level.
"""

from __future__ import annotations

import functools
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from corpai.classify import source_default
from corpai.config import load_yaml
from corpai.types import Dept, Level

MAX_METRICS = 30
UNITS = frozenset({"EUR", "pct", "count", "days"})
GRAINS = frozenset({"month", "quarter"})
STATUSES = ("draft", "signed", "published")

# Aggregate dimensions only. Anything naming a person (employee, owner, assignee, candidate, contact,
# handler, lead, user) is refused, which is what "no individual-level metric" (D7) means in code.
ALLOWED_DIMS = frozenset({"dept", "stage", "tier", "project_id"})
PERSON_HINTS = ("employee", "user", "owner", "assignee", "candidate", "contact", "handler", "lead",
                "manager", "recruiter", "name", "email", "person", "subject")

FINANCE_ACL = ("dept:finance", "head:finance", "role:ceo")
MANAGEMENT_ACL = ("role:ceo", "dept:finance") + tuple(f"head:{d.value}" for d in Dept)
# Only aggregate audiences may receive a published metric; never a single user or a reporting line.
_AUDIENCE_RE = re.compile(r"^(all|role:ceo|dept:(\{dept\}|[a-z]+)|head:(\{dept\}|[a-z]+))$")

ALLOWED_SOURCES_RE = re.compile(r"^(fin|crm|eng|legal|hr)\.[a-z_]+$|^core\.users$")
FORBIDDEN_SOURCES = frozenset({"hr.compensation", "hr.performance", "hr.screenings", "hr.candidates"})
PEOPLE_SOURCES = frozenset({"hr.employees", "core.users"})
PEOPLE_MIN_GROUP = 5

PARAMS = frozenset({"start", "end", "as_of", "periods", "next_start", "next_end", "ttm_start", "days"})
_PARAM_RE = re.compile(r"%\((\w+)\)s")
_TABLE_RE = re.compile(r"\b(?:from|join)\s+([a-z_]+\.[a-z_]+)", re.IGNORECASE)
_QUALIFIED_RE = re.compile(r"\b(synth|audit|ops|metrics|rag|lg|trace|pg_catalog|information_schema)\s*\.",
                           re.IGNORECASE)
_WRITE_RE = re.compile(r"\b(insert|update|delete|merge|truncate|drop|alter|create|grant|revoke|copy|call|do|"
                       r"set_config|pg_sleep|lo_import|dblink)\b", re.IGNORECASE)
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_QUOTED_IDENT_RE = re.compile(r'"((?:[^"]|"")*)"')
# Any schema-qualified name. Aliases ("j.dept", "rev.amount") are filtered out below by schema name.
_ANY_QUALIFIED_RE = re.compile(r"\b([a-z_][a-z0-9_$]*)\s*\.\s*([a-z_][a-z0-9_$]*)\b", re.IGNORECASE)
# Schemas a relation could live in: the business schemas plus everything a metric must never read.
_KNOWN_SCHEMAS = frozenset({"fin", "crm", "eng", "legal", "hr", "core", "synth", "audit", "ops", "metrics", "rag",
                            "lg", "trace", "public", "pg_catalog", "information_schema", "pg_temp", "pg_toast"})
# FROM / JOIN items and the comma-separated items that follow them ("FROM a x, b y").
_NOT_ALIAS = ("on", "where", "join", "left", "right", "inner", "full", "cross", "natural", "lateral", "using",
              "group", "order", "having", "window", "union", "except", "intersect", "limit", "offset", "fetch")
_FROM_ITEM = (r"([a-z_][a-z0-9_$]*(?:\s*\.\s*[a-z_][a-z0-9_$]*)?)(?:\s+(?:as\s+)?(?!(?:"
              + "|".join(_NOT_ALIAS) + r")\b)[a-z_][a-z0-9_$]*)?")
_FROM_LIST_RE = re.compile(r"\b(?:from|join)\s+" + _FROM_ITEM + r"((?:\s*,\s*" + _FROM_ITEM + r")*)",
                           re.IGNORECASE)
_FROM_ITEM_RE = re.compile(_FROM_ITEM, re.IGNORECASE)
_CTE_RE = re.compile(r"\b([a-z_][a-z0-9_$]*)\s+as\s*\(", re.IGNORECASE)


class MetricConfigError(ValueError):
    """config/metrics.yaml breaks a rule of the metric layer."""


@dataclass(frozen=True)
class MetricDef:
    id: str
    name_zh: str
    name_en: str
    definition: str
    formula: str
    unit: str
    dims: tuple[str, ...]
    sources: tuple[str, ...]
    owner_role: str
    level: Level
    visible_to: str | tuple[str, ...]
    sql: str
    sql_sha256: str
    grain: tuple[str, ...]
    min_group: int
    refresh: str
    workflow: tuple[str, ...]
    draft_acl: tuple[str, ...]
    published_level: Level
    dept: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def people_derived(self) -> bool:
        return bool(PEOPLE_SOURCES & set(self.sources))

    def name(self, lang: str = "en") -> str:
        return self.name_zh if lang == "zh" else self.name_en

    def published_acl(self, row_dept: str | None) -> tuple[str, ...]:
        """The audience a published row of this metric gets (architecture §5.1 fin.metric)."""
        if self.visible_to == "company":
            return ("all",)
        if self.visible_to == "management":
            return MANAGEMENT_ACL
        templates = (("dept:{dept}", "head:{dept}", "role:ceo", "dept:finance")
                     if self.visible_to == "dept" else self.visible_to)
        if row_dept is None:
            # A company-wide total of a per-dept metric belongs to the management audience.
            return MANAGEMENT_ACL
        return tuple(dict.fromkeys(t.replace("{dept}", row_dept) for t in templates))

    def public_dict(self) -> dict[str, Any]:
        """What list_metrics shows: the definition, never the SQL text (only its hash)."""
        return {"metric_id": self.id, "name_zh": self.name_zh, "name_en": self.name_en,
                "definition": self.definition, "formula": self.formula, "unit": self.unit,
                "dims": list(self.dims), "grain": list(self.grain), "owner_role": self.owner_role,
                "refresh": self.refresh, "level": int(self.level), "min_group": self.min_group,
                "visible_to": self.visible_to if isinstance(self.visible_to, str) else list(self.visible_to),
                "sources": list(self.sources), "sql_sha256": self.sql_sha256,
                "workflow": list(self.workflow)}


def sql_digest(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()


def _req(m: Mapping, key: str, mid: str) -> Any:
    if key not in m or m[key] in (None, ""):
        raise MetricConfigError(f"metric {mid}: missing {key!r}")
    return m[key]


def _normalise_sql(sql: str) -> str:
    """Comments removed and quoted identifiers unquoted (lower-cased), so `"synth"."planted"` and
    `/* */`-split names are seen exactly as Postgres resolves them."""
    body = _COMMENT_RE.sub(" ", sql)
    return _QUOTED_IDENT_RE.sub(lambda m: m.group(1).replace('""', '"').lower(), body)


def referenced_relations(sql: str) -> set[str]:
    """Every relation the SQL can read, as far as text can tell: schema-qualified names in a known
    schema anywhere in the statement (subqueries, EXISTS, comma joins), plus every FROM/JOIN item.
    Unqualified FROM items that are not CTE names come back as-is (they would resolve through the
    search_path) so the caller can refuse them. refresh.py re-checks with EXPLAIN on the database."""
    body = _normalise_sql(sql)
    out = {f"{a.lower()}.{b.lower()}" for a, b in _ANY_QUALIFIED_RE.findall(body) if a.lower() in _KNOWN_SCHEMAS}
    ctes = {c.lower() for c in _CTE_RE.findall(body)}
    for m in _FROM_LIST_RE.finditer(body):
        items = [m.group(1)] + [x.group(1) for x in _FROM_ITEM_RE.finditer(m.group(2) or "") if x.group(1)]
        for item in items:
            name = re.sub(r"\s+", "", item).lower()
            if "." in name or name not in ctes:
                out.add(name)
    return out


def _check_sql(mid: str, sql: str, sources: tuple[str, ...]) -> None:
    if '"' in _QUOTED_IDENT_RE.sub("", sql):
        raise MetricConfigError(f"metric {mid}: unbalanced quotes in sql")
    body = _normalise_sql(sql).strip().rstrip(";")
    if ";" in body:
        raise MetricConfigError(f"metric {mid}: sql must be a single statement")
    if not re.match(r"^(select|with)\b", body, re.IGNORECASE):
        raise MetricConfigError(f"metric {mid}: sql must start with SELECT or WITH")
    if _WRITE_RE.search(body):
        raise MetricConfigError(f"metric {mid}: sql contains a write or session command")
    if _QUALIFIED_RE.search(body):
        # The synth schema holds the evaluation answers: the app must never read them, not even as the owner.
        raise MetricConfigError(f"metric {mid}: sql references a schema outside the business data")
    unknown = set(_PARAM_RE.findall(body)) - PARAMS
    if unknown:
        raise MetricConfigError(f"metric {mid}: unknown sql parameters {sorted(unknown)}")
    if "%" in _PARAM_RE.sub("", body):
        raise MetricConfigError(f"metric {mid}: stray '%' in sql (use %% or a parameter)")
    tables = {t.lower() for t in _TABLE_RE.findall(body)} | referenced_relations(sql)
    if tables != set(sources):
        raise MetricConfigError(f"metric {mid}: sources {sorted(sources)} do not match the tables the sql reads "
                                f"{sorted(tables)}")


def _parse(m: Mapping, defaults: Mapping, workflows: Mapping) -> MetricDef:
    mid = str(_req(m, "id", "?"))
    if not _ID_RE.match(mid):
        raise MetricConfigError(f"metric id {mid!r} must be snake_case")
    unit = _req(m, "unit", mid)
    if unit not in UNITS:
        raise MetricConfigError(f"metric {mid}: unit must be one of {sorted(UNITS)}")
    dims = tuple(m.get("dims") or ())
    for d in dims:
        if d not in ALLOWED_DIMS or any(h in d for h in PERSON_HINTS):
            raise MetricConfigError(f"metric {mid}: dimension {d!r} is not an aggregate dimension (D7)")
    sources = tuple(_req(m, "sources", mid))
    for s in sources:
        if s in FORBIDDEN_SOURCES:
            raise MetricConfigError(f"metric {mid}: {s} may not feed a metric (pay, ratings, candidates)")
        if not ALLOWED_SOURCES_RE.match(s):
            raise MetricConfigError(f"metric {mid}: source {s!r} is outside the business schemas")
    sql = str(_req(m, "sql", mid)).strip()
    _check_sql(mid, sql, sources)
    level = Level(int(_req(m, "level", mid)))
    floor = max((source_default(s) for s in sources), default=Level.L4)
    if level < floor:
        raise MetricConfigError(f"metric {mid}: level L{int(level)} is below its sources' default L{int(floor)}")
    min_group = int(m.get("min_group", defaults.get("min_group", 1)))
    if min_group < 1:
        raise MetricConfigError(f"metric {mid}: min_group must be >= 1")
    if PEOPLE_SOURCES & set(sources) and min_group < PEOPLE_MIN_GROUP:
        raise MetricConfigError(f"metric {mid}: derived from people, needs min_group >= {PEOPLE_MIN_GROUP} (D7)")
    if min_group > 1 and not re.search(r"\bas\s+n\b", sql, re.IGNORECASE):
        raise MetricConfigError(f"metric {mid}: min_group > 1 needs an `n` column in the sql")
    visible = m.get("visible_to")
    if isinstance(visible, str):
        if visible not in ("company", "management", "dept"):
            raise MetricConfigError(f"metric {mid}: visible_to must be company, management, dept or a list")
        visible_to: str | tuple[str, ...] = visible
    elif isinstance(visible, list) and visible:
        for g in visible:
            if not isinstance(g, str) or not _AUDIENCE_RE.match(g):
                raise MetricConfigError(f"metric {mid}: audience {g!r} is not an aggregate group")
        visible_to = tuple(visible)
    else:
        raise MetricConfigError(f"metric {mid}: visible_to is required")
    dept = m.get("dept")
    if dept is not None:
        if dept not in {d.value for d in Dept}:
            raise MetricConfigError(f"metric {mid}: unknown dept {dept!r}")
        if "dept" in dims:
            raise MetricConfigError(f"metric {mid}: `dept` and a dept dimension are exclusive")
    needs_dept = visible_to == "dept" or (isinstance(visible_to, tuple) and any("{dept}" in g for g in visible_to))
    if needs_dept and dept is None and "dept" not in dims:
        raise MetricConfigError(f"metric {mid}: a dept audience needs `dept` or a dept dimension")
    draft_acl = tuple(m.get("draft_acl") or defaults.get("draft_acl") or FINANCE_ACL)
    if not draft_acl or not set(draft_acl) <= set(FINANCE_ACL):
        raise MetricConfigError(f"metric {mid}: draft_acl may only narrow {list(FINANCE_ACL)}")
    grain = tuple(m.get("grain") or defaults.get("grain") or ("month",))
    if not set(grain) <= GRAINS:
        raise MetricConfigError(f"metric {mid}: grain must be within {sorted(GRAINS)}")
    wf_name = m.get("workflow", defaults.get("workflow", "standard"))
    workflow = tuple(workflows.get(wf_name) or ())
    if workflow != STATUSES:
        raise MetricConfigError(f"metric {mid}: workflow must be {list(STATUSES)}")
    published_level = Level(int(m.get("published_level", defaults.get("published_level", 2))))
    known = {"id", "name_zh", "name_en", "definition", "formula", "unit", "dims", "sources", "owner_role",
             "level", "visible_to", "sql", "grain", "min_group", "refresh", "workflow", "draft_acl",
             "published_level", "dept"}
    return MetricDef(
        id=mid, name_zh=str(_req(m, "name_zh", mid)), name_en=str(_req(m, "name_en", mid)),
        definition=str(_req(m, "definition", mid)), formula=str(_req(m, "formula", mid)), unit=unit,
        dims=dims, sources=sources, owner_role=str(_req(m, "owner_role", mid)), level=level,
        visible_to=visible_to, sql=sql, sql_sha256=sql_digest(sql), grain=grain, min_group=min_group,
        refresh=str(m.get("refresh") or defaults.get("refresh") or "monthly"), workflow=workflow,
        draft_acl=draft_acl, published_level=published_level, dept=dept,
        extra={k: v for k, v in m.items() if k not in known})


def parse_config(cfg: Mapping) -> dict[str, MetricDef]:
    """Validate a parsed metrics.yaml document; raises MetricConfigError on the first problem."""
    if not isinstance(cfg, Mapping) or not isinstance(cfg.get("metrics"), list):
        raise MetricConfigError("metrics.yaml needs a `metrics:` list")
    items = cfg["metrics"]
    if len(items) > MAX_METRICS:
        raise MetricConfigError(f"at most {MAX_METRICS} metrics (design-review 二.8), got {len(items)}")
    defaults = cfg.get("defaults") or {}
    workflows = cfg.get("workflows") or {"standard": list(STATUSES)}
    out: dict[str, MetricDef] = {}
    for m in items:
        md = _parse(m, defaults, {k: tuple(v) for k, v in workflows.items()})
        if md.id in out:
            raise MetricConfigError(f"duplicate metric id {md.id!r}")
        out[md.id] = md
    return out


@functools.cache
def load_metrics() -> dict[str, MetricDef]:
    """config/metrics.yaml, validated, cached per process (config.reload_settings() does not clear it)."""
    return parse_config(load_yaml("metrics"))


def get_def(metric_id: str) -> MetricDef:
    defs = load_metrics()
    if metric_id not in defs:
        raise KeyError(f"unknown metric {metric_id!r}")
    return defs[metric_id]
