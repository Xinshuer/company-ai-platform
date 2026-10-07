"""Exports carry a visible watermark, a verifiable footer hash and an audit record."""

from __future__ import annotations

from dataclasses import replace

import pytest

from corpai.metrics import aexport_report, export_report, verify_export
from corpai.types import Level, Role
from tests.metrics.conftest import owner_rows

BODY = "## Key figures\n\n- Revenue: EUR 74,500\n- 收入：74,500 欧元\n"


def _last_export() -> dict:
    return owner_rows("SELECT * FROM audit.log WHERE event = 'export' ORDER BY seq DESC LIMIT 1")[0]


def test_export_has_watermark_hash_and_audit(seeded, personas):
    ceo = personas["ceo"]
    data, name = export_report(ceo, title="Management report 2026-09", body_md=BODY, run_id="run_test01",
                               level=Level.L3, domain="company")
    text = data.decode("utf-8")
    first = text.splitlines()[0]
    assert first.startswith("> WATERMARK | exported by u0001 ") and ceo.name in first
    assert "| run run_test01 |" in first and "L3 confidential" in first and " UTC " in first
    assert text.count(first) == 2          # top and bottom, so a cropped copy still carries it
    assert "<!-- corpai-export v1 sha256=" in text
    assert not any(ch in text for ch in "​‌‍⁠﻿")  # nothing hidden
    assert name.endswith(".md") and "run_test01" in name and "/" not in name and "\\" not in name
    check = verify_export(data)
    assert check.ok and check.reason == "intact"
    a = _last_export()
    assert a["actor"] == "u0001" and a["decision"] == "allow" and a["run_id"] == "run_test01"
    assert a["details"]["sha256"] == check.sha256 and a["details"]["filename"] == name
    assert "Revenue" not in str(a["details"])  # metadata only, never the report text


def test_tampered_export_fails_verification(seeded, personas):
    data, _ = export_report(personas["finance_head"], title="Close", body_md=BODY, run_id="run_test02",
                            domain="finance")
    edited = data.replace(b"74,500", b"84,500", 1)
    assert not verify_export(edited).ok
    rewatermarked = data.replace(personas["finance_head"].user_id.encode(), b"u0099")
    assert not verify_export(rewatermarked).ok
    assert not verify_export(b"plain text").ok


@pytest.mark.parametrize("change", [{"active": False}, {"role": Role.SERVICE}])
def test_inactive_and_service_accounts_cannot_export(seeded, personas, change):
    with pytest.raises(PermissionError):
        export_report(replace(personas["ceo"], **change), title="x", body_md="y", run_id="run_test03",
                      domain="company")


def test_policy_denies_export_outside_the_requesters_domain(seeded, personas):
    with pytest.raises(PermissionError):
        export_report(personas["sales_staff"], title="Finance report", body_md=BODY, run_id="run_test04",
                      domain="finance")
    a = _last_export()
    assert a["actor"] == personas["sales_staff"].user_id and a["decision"] == "deny"


def test_bad_run_id_is_refused(seeded, personas):
    with pytest.raises(ValueError):
        export_report(personas["ceo"], title="x", body_md="y", run_id="../../etc/passwd", domain="company")


@pytest.mark.anyio
async def test_async_export(seeded, personas):
    data, _ = await aexport_report(personas["ceo"], title="Board pack", body_md=BODY, run_id="run_test05",
                                 domain="company")
    assert verify_export(data).ok
    assert _last_export()["run_id"] == "run_test05"


def test_export_without_a_domain_is_refused(seeded, personas):
    """Review finding: with domain omitted the policy check was skipped and any active user could export."""
    with pytest.raises(TypeError):
        export_report(personas["sales_staff"], title="Finance report", body_md=BODY, run_id="run_test06")
    for bad in (None, "", "  "):
        with pytest.raises(ValueError):
            export_report(personas["sales_staff"], title="Finance report", body_md=BODY, run_id="run_test06",
                          domain=bad)


def test_watermark_level_cannot_be_lowered_by_the_caller(seeded, personas):
    """Review finding: level was taken from the caller, so level=1 labelled an L3/L4 report 'L1 public'."""
    data, _ = export_report(personas["ceo"], title="Board pack", body_md=BODY, run_id="run_test07",
                            domain="company", level=Level.L1, report_level=Level.L4)
    assert "L4 restricted" in data.decode("utf-8").splitlines()[0]
    assert _last_export()["level"] == 4
    # without the draft's level, the text's own classification is the floor (amounts are L3)
    data, _ = export_report(personas["ceo"], title="Board pack", body_md=BODY, run_id="run_test08",
                            domain="company", level=Level.L1)
    assert "L1 public" not in data.decode("utf-8")
