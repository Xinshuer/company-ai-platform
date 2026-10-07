"""dlp_scan (egress backstop) and mask_for_level (trace storage)."""

from __future__ import annotations

import pytest

from corpai.classify import DlpResult, dlp_scan, mask_for_level
from corpai.types import Level, Route

PUBLIC = "What is deferred revenue?"
INTERNAL = "The conference fee is €120 per person."
CONFIDENTIAL = "Call Anna Becker at +49 30 12345678 or anna@nordhafen-logistik.example about FleetPilot."
RESTRICTED = "Anna Becker's salary is €78,000"


# ---- dlp_scan ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text,level,blocked", [
    (PUBLIC, Level.L1, False),
    (INTERNAL, Level.L2, False),
    (CONFIDENTIAL, Level.L3, True),
    (RESTRICTED, Level.L4, True),
    ("KB-CANARY-0A1B2C3D", Level.L4, True),
    ("sk-abcdefghijklmnopqrstuvwxyz123456", Level.L4, True),
])
def test_external_blocked_from_l3(text, level, blocked):
    r = dlp_scan(text, route=Route.EXTERNAL)
    assert isinstance(r, DlpResult)
    assert r.level == level and r.blocked is blocked
    if blocked:
        assert r.findings


@pytest.mark.parametrize("text", [PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED, "KB-CANARY-0A1B2C3D"])
def test_local_never_blocked_but_level_reported(text):
    r = dlp_scan(text, route=Route.LOCAL)
    assert r.blocked is False
    assert r.level == dlp_scan(text, route=Route.EXTERNAL).level


def test_route_accepts_strings_and_unknown_routes_fail_closed():
    assert dlp_scan(CONFIDENTIAL, route="external").blocked
    assert not dlp_scan(CONFIDENTIAL, route="local").blocked
    for bogus in ("Local", "LOCAL ", "extern", "", None):
        assert dlp_scan(CONFIDENTIAL, route=bogus).blocked, bogus  # typo ≠ local
    assert not dlp_scan(PUBLIC, route="whatever").blocked  # L1 stays fine anywhere


def test_classifier_failure_blocks_external():
    r = dlp_scan(None, route=Route.EXTERNAL)  # type: ignore[arg-type]
    assert r.blocked and r.level == Level.L4
    assert [f.kind for f in r.findings] == ["classifier_error"]


# ---- mask_for_level ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,level", [(PUBLIC, Level.L1), (PUBLIC, Level.L2), (INTERNAL, Level.L2),
                                        ("```python\nprint(1)\n```", Level.L1)])
def test_l1_l2_unchanged(text, level):
    assert mask_for_level(text, level) == text


def test_l3_masks_every_l3_span():
    out = mask_for_level(CONFIDENTIAL, Level.L3)
    assert out is not None
    assert "Anna Becker" not in out and "12345678" not in out and "nordhafen-logistik" not in out
    assert "⟨person_name⟩" in out and "⟨phone⟩" in out and "⟨email⟩" in out
    assert out.startswith("Call ⟨person_name⟩ at ⟨phone⟩")
    assert "FleetPilot" in out  # a product name is not personal data


def test_l3_keeps_l2_content_and_masks_commercial_money():
    out = mask_for_level("The taxi was €120 on the way to the airport, then we talked about the weather. "
                         "Later: the discount quote is €12,500 for Piotr Nowak.", Level.L3)
    assert out is not None
    assert "€120" in out  # bare money is L2
    assert "12,500" not in out and "Piotr Nowak" not in out


def test_l3_masks_code_with_company_identifiers():
    text = "See:\n```\nimport kb_billing\nkb_billing.charge()\n```\ndone"
    out = mask_for_level(text, Level.L3)
    assert out is not None and "kb_billing" not in out and "⟨code⟩" in out and out.endswith("done")


def test_l4_stores_nothing():
    assert mask_for_level(PUBLIC, Level.L4) is None
    assert mask_for_level(RESTRICTED, Level.L4) is None


@pytest.mark.parametrize("text", [RESTRICTED, "KB-CANARY-0A1B2C3D in the doc",
                                  "password=hunter2", "Is there an RCE in FleetPilot?"])
@pytest.mark.parametrize("label", [Level.L1, Level.L2, Level.L3])
def test_content_raises_the_level_never_lowers_it(text, label):
    """A span labelled too low still never stores restricted content (rule 2: the higher level wins)."""
    assert mask_for_level(text, label) is None


def test_l2_label_with_l3_content_gets_masked():
    out = mask_for_level("Ping Jonas Weber", Level.L2)
    assert out == "Ping ⟨person_name⟩"


@pytest.mark.parametrize("bad", [0, 5, "L9", "high", None, 2.5])
def test_invalid_level_fails_closed(bad):
    assert mask_for_level(PUBLIC, bad) is None


def test_none_text():
    assert mask_for_level(None, Level.L1) is None  # type: ignore[arg-type]


def test_accepts_int_and_string_levels():
    assert mask_for_level(PUBLIC, 1) == PUBLIC
    assert mask_for_level(PUBLIC, "L2") == PUBLIC
    assert mask_for_level(PUBLIC, 4) is None
