"""Shared test data for policy tests: cases.yaml parsed into Resources, and an ACL group pool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from corpai.types import Dept, Label, Level, Resource, Trust
from tests.policy.fake_personas import resolve

CASES = yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text(encoding="utf-8"))

GROUP_POOL = ["all", "dept:sales", "dept:eng", "dept:legal", "dept:finance", "dept:hr", "dept:exec", "dept:it",
              "head:sales", "head:eng", "head:legal", "head:finance", "head:hr", "head:exec", "role:ceo",
              "role:platform_admin", "role:security_auditor", "user:u0004", "user:u0008", "proj:P-01",
              "proj:P-02", "matter:M-01", "req:R-01", "mgr-of:u0006", "mgr-of:u0004", "grant:crm:A-0012",
              "svc:metrics"]


def build_resource(name: str, spec: dict[str, Any]) -> Resource:
    spec = resolve(spec)
    label = Label(level=Level(spec["level"]), dept=Dept(spec["dept"]) if spec.get("dept") else None,
                  acl_groups=frozenset(spec.get("acl") or ()), trust=Trust(spec.get("trust", "internal")),
                  sources=(f"{spec['kind']}:{name}",))
    return Resource(kind=spec["kind"], id=name, label=label, attrs=spec.get("attrs") or {})


RESOURCES = {name: build_resource(name, spec) for name, spec in CASES["resources"].items()}
