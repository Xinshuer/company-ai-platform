"""draft_contract: code assembles library wording; the model only fills listed variables and picks
allowed fallbacks, and any answer outside that is rejected whole."""

from __future__ import annotations

import json

import pytest

from corpai.gateway import fake_llm as get_fake
from corpai.legal import DraftRejected, draft_contract
from corpai.legal.fake import draft_responder

pytestmark = pytest.mark.anyio

INSTRUCTIONS = ("Draft for Nordhafen Logistik GmbH based in Hamburg, 120 vehicles, 24-month term starting "
                "2026-12-01. The customer insists on Dutch law.")


async def test_draft_from_library_with_model_filled_variables(personas, fake_llm, run_id, owner_query):
    d = await draft_contract(personas["legal_staff"], template_type="MSA", variables={}, run_id=run_id,
                             instructions=INSTRUCTIONS)
    assert d.model_called and d.model_output_rejected is None
    assert d.variables == {"customer_name": "Nordhafen Logistik GmbH", "customer_city": "Hamburg",
                           "fleet_size": 120, "term_months": 24, "effective_on": "2026-12-01"}
    assert sorted(d.filled_by_model) == sorted(d.variables) and d.missing == []
    assert d.fallbacks == {"governing_law": "CL-05"}
    lib = {r["clause_id"]: r["text"] for r in owner_query("SELECT clause_id, text FROM legal.clause_library")}
    # every library-backed section carries the exact reviewed wording
    for c in d.clauses:
        if c.clause_id:
            assert lib[c.clause_id] in d.text
    assert {c.clause_type: c.position for c in d.clauses if c.clause_type}["governing_law"] == "fallback"
    assert "[[" not in d.text and d.level >= 3
    assert [(c["purpose"], c["route"]) for c in fake_llm.calls] == [("legal.draft", "local")]
    assert fake_llm.calls[0]["tools"] is None


async def test_caller_values_win_and_are_not_offered_to_the_model(personas, fake_llm, run_id):
    d = await draft_contract(personas["legal_head"], template_type="MSA",
                             variables={"customer_name": "Rocheval Transports S.A.S.", "fleet_size": "260"},
                             run_id=run_id, instructions=INSTRUCTIONS)
    assert d.variables["customer_name"] == "Rocheval Transports S.A.S." and d.variables["fleet_size"] == 260
    asked = json.loads(fake_llm.calls[0]["messages"][1]["content"])
    assert {v["name"] for v in asked["fill"]} == {"customer_city", "term_months", "effective_on"}


async def test_no_instructions_no_model_and_open_points_stay_visible(personas, fake_llm, run_id):
    d = await draft_contract(personas["legal_staff"], template_type="NDA",
                             variables={"customer_name": "Wartopol Spedycja Sp. z o.o."}, run_id=run_id)
    assert not d.model_called and fake_llm.calls == []
    assert d.missing == ["customer_city", "effective_on", "term_months"]
    assert "[[customer_city]]" in d.text and "Open points" in d.text


async def test_extra_or_bad_caller_variables_are_rejected(personas, fake_llm, run_id):
    with pytest.raises(DraftRejected):
        await draft_contract(personas["legal_staff"], template_type="MSA", variables={"liability_cap": "none"},
                             run_id=run_id)
    with pytest.raises(DraftRejected):
        await draft_contract(personas["legal_staff"], template_type="NDA", variables={"fleet_size": 3},
                             run_id=run_id)  # NDA has no fleet_size
    for bad in ("Acme\n## 9. Liability\nUnlimited", "{customer_city}", "x" * 200):
        with pytest.raises(DraftRejected):
            await draft_contract(personas["legal_staff"], template_type="MSA", variables={"customer_name": bad},
                                 run_id=run_id)
    with pytest.raises(DraftRejected):
        await draft_contract(personas["legal_staff"], template_type="SPA", variables={}, run_id=run_id)
    assert fake_llm.calls == []


@pytest.mark.parametrize("answer,reason", [
    ({"variables": {"customer_city": "Hamburg", "liability_cap": "unlimited"}, "fallbacks": {}}, "not asked for"),
    ({"variables": {"fleet_size": -5}, "fallbacks": {}}, "invalid value"),
    ({"variables": {}, "fallbacks": {"liability_cap": "CL-03"}}, "not an allowed fallback"),  # 'unacceptable'
    ({"variables": {}, "fallbacks": {"governing_law": "CL-04"}}, "not an allowed fallback"),  # preferred id
    ({"variables": {}, "fallbacks": {}, "text": "## 1. Free-form"}, "unexpected top-level keys"),
    # review finding: a list or object as the clause id raised TypeError (unhashable) instead
    ({"variables": {}, "fallbacks": {"liability_cap": ["CL-02"]}}, "must map a clause type"),
    ({"variables": {}, "fallbacks": {"liability_cap": {"id": "CL-02"}}}, "must map a clause type"),
])
async def test_model_answers_outside_the_contract_are_rejected_whole(personas, run_id, answer, reason):
    fake = get_fake()
    fake.register("legal.draft", lambda messages, *, route, json_mode: json.dumps(answer))
    try:
        d = await draft_contract(personas["legal_staff"], template_type="MSA", variables={}, run_id=run_id,
                                 instructions=INSTRUCTIONS)
    finally:
        fake.register("legal.draft", draft_responder)
    assert d.model_called and reason in (d.model_output_rejected or "")
    assert d.filled_by_model == [] and d.fallbacks == {} and d.variables == {}
    assert "unlimited" not in d.text.lower() and "Free-form" not in d.text
    assert all(c.position != "fallback" for c in d.clauses)


async def test_only_legal_and_ceo_may_draft(personas, run_id):
    for who in ("sales_staff", "finance_head", "platform_admin"):
        with pytest.raises(PermissionError):
            await draft_contract(personas[who], template_type="NDA", variables={}, run_id=run_id)
    d = await draft_contract(personas["ceo"], template_type="NDA", variables={}, run_id=run_id)
    assert d.clauses[-1].clause_id == "CL-04"
