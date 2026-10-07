"""design-review 五 permission matrix (tests/security/permission_matrix.yaml) against decide().

The integration stream adds the RLS side for the same cases on seeded data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corpai.policy import LABELLED_KINDS, decide
from corpai.types import Action, Dept, Label, Level, Resource, Trust
from tests.policy.fake_personas import principal, resolve

pytestmark = pytest.mark.security

MATRIX = yaml.safe_load((Path(__file__).parent / "permission_matrix.yaml").read_text(encoding="utf-8"))
COLUMNS = {"staff", "head", "other", "ceo", "platform_admin"}


def _resource(name: str) -> Resource:
    spec = resolve(MATRIX["resources"][name])
    label = Label(level=Level(spec["level"]), dept=Dept(spec["dept"]) if spec.get("dept") else None,
                  acl_groups=frozenset(spec.get("acl") or ()), trust=Trust(spec.get("trust", "internal")))
    return Resource(kind=spec["kind"], id=name, label=label, attrs=spec.get("attrs") or {})


CASES = [
    pytest.param(row["row"], c, id=f"{row['row'].split(' ')[0]}-{c['persona']}-{c['action']}-{c['resource']}")
    for row in MATRIX["rows"] for c in row["cases"]
]


def test_every_row_covers_every_column():
    assert len(MATRIX["rows"]) == 12
    for row in MATRIX["rows"]:
        assert {c["column"] for c in row["cases"]} == COLUMNS, row["row"]
        for c in row["cases"]:
            assert c["resource"] in MATRIX["resources"] and c["expect"] in ("allow", "deny")


def test_labelled_resources_follow_contract():
    for name in MATRIX["resources"]:
        r = _resource(name)
        if r.kind in LABELLED_KINDS:
            assert r.label.acl_groups, f"{name}: labelled rows always carry an ACL"


@pytest.mark.parametrize("row,case", CASES)
def test_matrix_case(row, case):
    d = decide(principal(case["persona"]), Action(case["action"]), _resource(case["resource"]))
    assert d.allow is (case["expect"] == "allow"), f"{row} / {case['cell']}: {d}"
    assert d.obligations == frozenset(case.get("obligations") or ()), d
    assert d.field_mask == frozenset(case.get("field_mask") or ()), d
    assert d.rule_id and d.reason
