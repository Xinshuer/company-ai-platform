"""What a demo viewer sees on the pages themselves (wiring review findings on the demo).

tests/ui/views_harness.js renders the real views on a small fake DOM (tests/ui/fake_dom.js) under
Node and reports badges, notes and controls; skipped when Node is not installed.

- external_down: every route badge inside a run follows the run to local (a step chip or a note
  badge that still says "external" during an outage drill claims a call went out);
- the offline fake model's text is labelled next to the text;
- a persona with no recording of an app is told so up front; one with recordings is not;
- controls that could only answer "no recording" are disabled and marked, recorded ones work;
- the head of finance can compute a first period when no period has metric snapshots yet.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("views_harness.js")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def r() -> dict:
    proc = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_external_down_moves_every_badge_of_the_run_to_local(r):
    # without the switch the recordings went external (so the check below means something)
    assert "external" in [x for c in r["sales_steps_up"] for x in c["routes"]]
    assert r["hc_note_up"]["routes"] == ["external"] and r["hc_text_up"].startswith("[fake/external]")
    # with external_down: the run, its steps and the note all say local
    assert r["sales_head_route_down"] == ["local"] and r["hc_head_route_down"] == ["local"]
    assert [x for c in r["sales_steps_down"] for x in c["routes"]] == ["local"]
    assert r["hc_note_down"]["routes"] == ["local"] and r["t"]["route_external"] not in r["hc_note_down"]["text"]
    assert "[fake/external]" not in r["hc_text_down"]


def test_fake_model_text_is_labelled(r):
    assert r["sales_fake_note"] and r["report_fake_note"] and r["hc_fake_note"]


def test_personas_without_a_recording_are_told_up_front(r):
    cov = r["coverage"]
    for k in ("legal_head@legal", "ceo@legal", "sales_head@sales"):
        assert cov[k] is True, k
    for k in ("legal_staff@legal", "sales_staff@sales", "ceo@sales", "legal_head@report", "hr_head@report",
              "ceo@report", "hr_head@hr", "hr_staff@hr"):
        assert cov[k] is False, k


def test_screening_buttons_work_exactly_where_a_recording_exists(r):
    for persona, rows in r["screen_buttons"].items():
        assert rows, persona
        assert all(row["disabled"] == (not row["recorded"]) for row in rows), (persona, rows)
        assert any(row["disabled"] for row in rows) and any(not row["disabled"] for row in rows), persona
    head = {row["id"] for row in r["screen_buttons"]["hr_head"] if not row["disabled"]}
    assert head == {"CAND-001", "CAND-007", "CAND-013"}
    # a disabled button starts nothing; an enabled one never answers "no recording"
    assert r["t"]["hr_workspace_empty"] in r["disabled_click_text"]
    assert r["enabled_click_error"] is False


def test_sales_page_offers_recorded_companies_and_the_recorded_follow_up(r):
    assert "Elbstrom Paketdienst GmbH" in r["sales_chips"] and "Havenmeer Distributie B.V." in r["sales_chips"]
    dl = {o["value"]: o["unrecorded"] for o in r["sales_datalist"]}
    assert dl["Elbstrom Paketdienst GmbH"] is False and dl["Donauzeile Frischdienst GmbH"] is True
    assert r["followup_value"] == "CT-00004"
    opts = r["followup_options"]
    assert opts[0] == {**opts[0], "value": "CT-00004", "disabled": False}
    assert len(opts) > 1 and all(o["disabled"] and r["t"]["demo_unrecorded_suffix"] in o["text"] for o in opts[1:])


def test_legal_picker_offers_only_reviews_that_were_recorded(r):
    assert r["legal_pick_enabled"] == ["C-0004", "C-0005"] and r["legal_pick_value"] == "C-0004"
    assert r["legal_pick_n"] > 2


def test_cfo_can_compute_a_first_period_from_the_page(r):
    assert r["t"]["metrics_no_periods_cfo"] in r["cfo_empty_text"]
    assert r["cfo_refresh_options"] == ["2026-09", "2026-08", "2026-Q3"] and r["cfo_has_refresh_button"]
    assert r["cfo_calls"] == ['GET /api/metrics', 'POST /api/metrics/refresh {"period":"2026-08"}', 'GET /api/metrics',
                              'GET /api/metrics/board?period=2026-08']
    assert r["t"]["metrics_no_periods_cfo"] not in r["cfo_after_text"]
    # without the right to refresh: the plain note, no period control
    assert r["t"]["metrics_no_periods"] in r["staff_empty_text"] and r["staff_selects"] == 0
