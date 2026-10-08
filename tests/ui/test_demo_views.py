"""What a demo viewer sees on the pages themselves (wiring review findings on the demo).

tests/ui/views_harness.js renders the real views on a small fake DOM (tests/ui/fake_dom.js) under
Node and reports badges, notes and controls; skipped when Node is not installed.

- external_down: every route badge inside a run follows the run to local (a step chip or a note
  badge that still says "external" during an outage drill claims a call went out);
- recorded model text says next to it that it is a recording of the real model (and when);
- every persona who may start an app has a recording; one without would be told so up front;
- controls that could only answer "no recording" are disabled and marked, recorded ones work;
- the head of finance can compute a first period when no period has metric snapshots yet;
- the low UI findings WR-12 .. WR-19 (issues-log 十三): empty requisitions, one alert for refused
  ones, tab keyboard and focus, translated codes, the hiring manager's bias notice, the refused
  intent step, metrics in the navigation of the platform roles, translated metric errors.
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
    assert r["hc_note_up"]["routes"] == ["external"] and r["hc_text_up"].strip()
    # with external_down: the run, its steps and the note all say local
    assert r["sales_head_route_down"] == ["local"] and r["hc_head_route_down"] == ["local"]
    assert [x for c in r["sales_steps_down"] for x in c["routes"]] == ["local"]
    assert r["hc_note_down"]["routes"] == ["local"] and r["t"]["route_external"] not in r["hc_note_down"]["text"]
    assert r["hc_text_down"] == r["hc_text_up"], "the drill changes the route, not the recorded text"


def test_recorded_model_text_is_labelled_as_a_recording(r):
    assert r["sales_fake_note"] and r["report_fake_note"] and r["hc_fake_note"]
    assert "2026-10-08" in r["t"]["demo_recorded_note"] and "DeepSeek" in r["t"]["demo_recorded_note"]


def test_personas_without_a_recording_are_told_up_front(r):
    cov = r["coverage"]
    # issues-log WR-07: every persona who may start these apps has a recording now
    for k in ("legal_head@legal", "ceo@legal", "legal_staff@legal", "sales_head@sales", "sales_staff@sales",
              "ceo@sales", "legal_head@report", "hr_head@report", "ceo@report", "hr_head@hr", "hr_staff@hr"):
        assert cov[k] is False, k
    # with one persona's recordings taken away, that persona (and only that one) is told up front
    assert cov["legal_head@legal-unrecorded"] is True and cov["sales_head@sales-unrecorded"] is True
    assert cov["legal_staff@legal-unrecorded"] is False


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


# ---- low UI findings of the wiring review (issues-log 十三, WR-12 .. WR-19)

def test_refused_requisitions_share_one_alert(r):
    # WR-19: the CEO may list the open requisitions but see none of their candidates
    assert len(r["ceo_cand_alerts"]) == 1
    for req in ("R-01", "R-02", "R-03", "R-04", "R-06"):
        assert req in r["ceo_cand_alerts"][0], req
    assert r["ceo_cand_cards"] == 0


def test_an_own_requisition_without_candidates_is_empty_not_denied(r):
    # WR-12: the hiring manager's own requisition says "no candidates yet"; someone else's empty
    # one says it may be either; neither is an alert
    text = r["empty_req"]["text"]
    assert r["empty_req"]["alerts"] == 0 and r["t"]["denied_title"] not in text
    assert r["t"]["hr_req_empty"] in text and r["t"]["hr_req_unseen"] in text


def test_hiring_manager_resume_view_has_the_bias_notice(r):
    # WR-17: shown with the résumé on #/candidates; the HR page keeps its own note
    assert r["t"]["hr_resume_bias_hm"] in r["hm_resume_text"]
    assert r["hr_page_has_hm_note"] is False
    # WR-14: candidate sources in the page's language
    assert r["hm_sources"] and all(s in r["t"]["sources"] for s in r["hm_sources"]), r["hm_sources"]


def test_refused_intent_step_shows_the_refusal_not_l3(r):
    # WR-18: Elbstrom's intent read was refused (the session stays L1); Havenmeer's was not
    assert r["t"]["sales_step_refused"] in r["intent_chip_Elbstrom"] and "L3" not in r["intent_chip_Elbstrom"]
    assert "L3" in r["intent_chip_Havenmeer"] and r["t"]["sales_step_refused"] not in r["intent_chip_Havenmeer"]
    # WR-14: industry and signal codes in the page's language
    assert r["t"]["sales_ind_parcel_delivery"] in r["sales_text_Elbstrom"] and "parcel_delivery" not in r["sales_text_Elbstrom"]
    assert r["t"]["sales_ind_retail_distribution"] in r["sales_text_Havenmeer"]
    assert r["t"]["sales_sig_fleet_expansion"] in r["sales_text_Havenmeer"] and "fleet_expansion" not in r["sales_text_Havenmeer"]


def test_legal_tabs_follow_the_aria_tabs_pattern(r):
    # WR-13: ids, one tab stop, controls/labelledby pairs, arrow keys, focus kept on the chosen tab
    tabs = r["tabs"]
    assert [t["id"] for t in tabs] == ["legal-tab-review", "legal-tab-draft", "legal-tab-due", "legal-tab-reviews"]
    assert [t["selected"] for t in tabs] == ["true", "false", "false", "false"]
    assert [t["tabindex"] for t in tabs] == ["0", "-1", "-1", "-1"]
    assert {t["controls"] for t in tabs} == {r["tabpanel"]["id"]} and r["tabpanel"]["labelledby"] == "legal-tab-review"
    keys = r["tab_keys"]
    assert keys["right"] == {"focused": "legal-tab-draft", "prevented": True}
    assert keys["left"] == {"focused": "legal-tab-reviews", "prevented": True}
    assert keys["end"]["focused"] == "legal-tab-reviews" and keys["home"]["focused"] == "legal-tab-review"
    assert keys["other"] == {"focused": None, "prevented": False}
    assert r["after_click"] == {"selected": "legal-tab-due", "focused": True, "state": "due"}


def test_legal_codes_and_requesters_read_in_the_page_language(r):
    # WR-14: contract type and status, the "what to do" column, the requester's name
    assert r["t"]["legal_ctype_order_form"] in r["legal_option"] and r["t"]["legal_cstatus_signed"] in r["legal_option"]
    assert "order_form" not in r["legal_option"]
    assert r["due_cells"] and r["due_cells"][-1] == r["t"]["due_action"]
    assert r["t"]["legal_ctype_order_form"] in r["due_cells"]
    assert "Viktor Sandberg" in r["reviews_text"] and "u0031" not in r["reviews_text"]


def test_metrics_is_not_offered_to_the_platform_roles(r):
    # issues-log 十三 📌: the page refuses them explicitly; the navigation and home no longer offer it
    for persona in ("platform_admin", "auditor"):
        assert r["nav"][persona] == {"metrics_link": False, "home_card": False}, persona
    for persona in ("ceo", "sales_staff"):
        assert r["nav"][persona] == {"metrics_link": True, "home_card": True}, persona


def test_metric_errors_are_named_in_the_page_language(r):
    # WR-14: the toast names the error; the server's English words stay beside it as "server says"
    assert r["refresh_fail"]["toast"] == r["t"]["err_bad_request"]
    assert r["t"]["err_bad_request"] in r["refresh_fail"]["text"]
    assert "period 2026-09 has no ledger rows yet" in r["refresh_fail"]["text"]
