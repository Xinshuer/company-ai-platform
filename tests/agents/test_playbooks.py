"""Playbooks: the active version is the one used, drafts and retired versions never are, levels
never sit below the content, the ACL of §5.1 applies, malformed files fail loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from corpai.playbooks import PlaybookError, PlaybookNotFound, list_playbooks, load_playbook, load_versions
from corpai.types import Dept, Level, Principal, Role

BASE = """id: {id}
title: Test playbook
dept: sales
owner_role: manager
version: {version}
status: {status}
level: {level}
eval_set: null
body:
  rule: {rule}
"""


def _write(tmp: Path, pid: str, *docs: dict) -> Path:
    text = "---\n".join(BASE.format(id=pid, **{"level": "L2", "rule": "be kind", **d}) for d in docs)
    path = tmp / f"{pid}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_playbooks_load_with_refs():
    sales = load_playbook("sales.followup")
    assert sales.ref == "sales.followup@1" and sales.status == "active" and sales.level == Level.L3
    assert set(sales.body["tiers"]) == {"high", "medium", "low"}
    eng = load_playbook("eng.stage_gates")
    assert eng.version == 2 and eng.ref == "eng.stage_gates@2" and eng.dept is Dept.ENG
    assert "测试" in eng.body["gates"]


def test_status_filtering_and_versions():
    versions = load_versions("eng.stage_gates")
    assert [(v.version, v.status) for v in versions] == [(1, "retired"), (2, "active"), (3, "draft")]
    assert load_playbook("eng.stage_gates", version=3).status == "draft"  # readable on purpose (review)
    active = {p.ref for p in list_playbooks()}
    assert {"eng.stage_gates@2", "sales.followup@1"} <= active and "eng.stage_gates@3" not in active
    assert {p.ref for p in list_playbooks(dept="eng", statuses=("draft",))} == {"eng.stage_gates@3"}
    with pytest.raises(PlaybookNotFound):
        load_playbook("eng.stage_gates", version=9)


def test_no_active_version_is_not_found(tmp_path):
    _write(tmp_path, "t.only_draft", {"version": 1, "status": "draft"})
    with pytest.raises(PlaybookNotFound):
        load_playbook("t.only_draft", directory=tmp_path)


@pytest.mark.parametrize("docs,match", [
    (({"version": 1, "status": "active"}, {"version": 2, "status": "active"}), "more than one active"),
    (({"version": 1, "status": "active"}, {"version": 1, "status": "draft"}), "duplicate"),
    (({"version": 0, "status": "active"},), "positive"),
    (({"version": 1, "status": "live"},), "status"),
    (({"version": 1, "status": "active", "level": "L9"},), "level"),
])
def test_malformed_playbooks_fail_loudly(tmp_path, docs, match):
    _write(tmp_path, "t.bad", *docs)
    with pytest.raises(PlaybookError, match=match):
        load_playbook("t.bad", directory=tmp_path)


def test_id_must_match_file_and_be_safe(tmp_path):
    (tmp_path / "t.renamed.yaml").write_text(BASE.format(id="t.other", version=1, status="active", level="L2",
                                                         rule="x"), encoding="utf-8")
    with pytest.raises(PlaybookError):
        load_playbook("t.renamed", directory=tmp_path)
    for bad in ("../secrets", "a/b", "", "UPPER"):
        with pytest.raises(PlaybookNotFound):
            load_playbook(bad, directory=tmp_path)


def test_level_is_never_below_the_content(tmp_path):
    _write(tmp_path, "t.leaky", {"version": 1, "status": "active", "level": "L2",
                                 "rule": "escalate to anna.becker@kestrelbyte.example"})
    pb = load_playbook("t.leaky", directory=tmp_path)
    assert pb.declared_level == Level.L2 and pb.level >= Level.L3


def _p(dept: Dept, role: Role = Role.EMPLOYEE, *, clearance: Level = Level.L3) -> Principal:
    groups = {"all", f"dept:{dept}"} | ({f"head:{dept}"} if role is Role.MANAGER else set())
    return Principal(user_id="u0999", name="x", email="x@kestrelbyte.example", dept=dept, role=role, title="t",
                     manager_id=None, clearance=clearance, groups=frozenset(groups))


def test_reading_a_playbook_checks_its_acl():
    assert load_playbook("sales.followup", principal=_p(Dept.SALES)).ref == "sales.followup@1"
    with pytest.raises(PermissionError):
        load_playbook("sales.followup", principal=_p(Dept.ENG))  # not in dept:sales/head:sales/role:ceo
    with pytest.raises(PermissionError):
        load_playbook("sales.followup", principal=_p(Dept.SALES, clearance=Level.L2))  # L3 above clearance
