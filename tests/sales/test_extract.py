"""Web extraction (D5, design-review 二.6): public pages only, L1 + public_only → external, fixed schema out.

Properties proved here:
- published company facts (funding amounts included) route external with hwm L1 and public_only;
- a page that carries a person's name falls back to local, and the name never reaches the profile;
- the planted injected pages change nothing in the profile, and even a model that obeys them
  cannot get anything beyond the schema fields (no extra keys, attacker URLs, tool calls);
- the prompt holds page text only: no CRM data;
- only sales / the CEO may run it; unpublished pages are not used.
"""

from __future__ import annotations

import json

import pytest

from corpai.classify import classify_text
from corpai.gateway import fake_llm as get_fake
from corpai.gateway.gateway import message_text
from corpai.sales import (
    CompanyProfile,
    NoPermission,
    extract_profile,
    extract_profile_with_meta,
    pages_for,
)
from corpai.sales.extract import PURPOSE
from corpai.synth import web as synth_web
from tests.sales.conftest import owner_rows

pytestmark = pytest.mark.anyio

FUNDED = "A-0012"  # a non-injected account whose funding page carries a money amount (seed 7, small)
INJECTED = {synth_web.INJECT_VISIBLE: "\n\nIMPORTANT INSTRUCTION", synth_web.INJECT_COMMENT: " <!--",
            synth_web.INJECT_FOOTNOTE: "\n\n[1] Company filings"}


def _model_call(fake, purpose: str = PURPOSE) -> dict:
    calls = [c for c in fake.calls if c["purpose"] == purpose]
    assert len(calls) == 1, calls
    return calls[0]


def _audit(run_id: str) -> list[dict]:
    return owner_rows("SELECT * FROM audit.log WHERE run_id = %s AND event = 'model_call' ORDER BY seq", (run_id,))


async def test_public_pages_route_external_with_public_only(personas, web_pages, fake_llm, run_id):
    pages = pages_for(FUNDED, web_pages)
    assert any("EUR" in p["text"] for p in pages), "the test needs a published money amount"
    profile, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=FUNDED, pages=pages,
                                                    run_id=run_id)
    call = _model_call(fake_llm)
    assert call["route"] == "external" and call["level"] == 1
    assert call["json_mode"] is True and call["tools"] is None
    assert meta.route == "external" and meta.public_only is True
    (row,) = _audit(run_id)
    assert row["route"] == "external" and row["details"]["public_only"] is True and row["details"]["hwm"] == 1
    # the gateway classified the prompt as public: money findings capped at L1 (X-01)
    assert classify_text(message_text(call["messages"]), public=True).level == 1
    assert profile.company == "Marevia Distribuzione S.r.l." and profile.hq_country == "IT"
    assert profile.last_funding_eur and profile.funding_stage.value in ("growth", "venture")
    assert set(profile.sources) <= {p["url"] for p in pages} and profile.sources


async def test_person_name_on_a_page_falls_back_to_local_and_is_dropped(personas, web_pages, fake_llm, run_id):
    (contact,) = owner_rows("SELECT name FROM crm.contacts WHERE account_id = %s ORDER BY contact_id LIMIT 1",
                            (FUNDED,))
    name = contact["name"]
    pages = pages_for(FUNDED, web_pages)
    extra = {**pages[0], "url": pages[0]["url"] + "#team", "kind": "news",
             "text": f"Managing director {name} said route planning is the next bottleneck."}
    profile, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=FUNDED,
                                                    pages=[*pages, extra], run_id=run_id)
    call = _model_call(fake_llm)
    assert call["route"] == "local" and call["level"] >= 3
    assert meta.route == "local"
    assert name in json.dumps(call["messages"])  # the page reached the model, but only on the local route
    assert name not in profile.model_dump_json()


async def test_company_field_cannot_carry_a_person_name(personas, web_pages, fake_llm, run_id):
    (contact,) = owner_rows("SELECT name FROM crm.contacts WHERE account_id = %s ORDER BY contact_id LIMIT 1",
                            (FUNDED,))
    pages = pages_for(FUNDED, web_pages)
    named = [{**pages[0], "text": pages[0]["text"] + f" Founded by {contact['name']}."}, *pages[1:]]
    fake_llm.register(PURPOSE, lambda m, **kw: json.dumps({"company": contact["name"]}))
    profile = await extract_profile(personas["sales_staff"], account_id=FUNDED, pages=named, run_id=run_id)
    assert profile.company != contact["name"] and contact["name"] not in profile.model_dump_json()


def _without_injection(pages: list[dict], account_id: str) -> list[dict]:
    marker = INJECTED[account_id]
    out = []
    for p in pages:
        if p["injected"]:
            cut = p["text"].index(marker)
            p = {**p, "text": p["text"][:cut], "injected": False}
        out.append(p)
    return out


@pytest.mark.parametrize("account_id", sorted(INJECTED))
async def test_injected_pages_have_no_effect(personas, web_pages, fake_llm, run_id, account_id):
    pages = pages_for(account_id, web_pages)
    assert any(p["injected"] for p in pages)
    injected, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=account_id, pages=pages,
                                                     run_id=run_id)
    clean, clean_meta = await extract_profile_with_meta(personas["sales_staff"], account_id=account_id,
                                                        pages=_without_injection(pages, account_id), run_id=run_id)
    assert injected == clean
    assert meta.flagged_urls == tuple(p["url"] for p in pages if p["injected"])
    assert clean_meta.flagged_urls == ()
    dumped = injected.model_dump_json()
    assert synth_web.ATTACKER_DOMAIN not in dumped and "@" not in dumped
    assert set(injected.model_dump()) == set(CompanyProfile.model_fields)


async def test_compromised_model_output_is_confined_to_the_schema(personas, web_pages, fake_llm, run_id):
    account_id = synth_web.INJECT_VISIBLE
    pages = pages_for(account_id, web_pages)
    real = pages[0]["url"]

    def obedient(messages, *, route, json_mode):  # a model that follows the injected page
        return {"content": json.dumps({
            "company": f"collector@{synth_web.ATTACKER_DOMAIN}", "domain": synth_web.ATTACKER_DOMAIN,
            "industry": "parcel_delivery", "growth_signals": ["hiring", "send_email"],
            "sources": [f"https://{synth_web.ATTACKER_DOMAIN}/collect", real], "last_funding_eur": "all contacts",
            "action": "crm.send_email", "to": f"collector@{synth_web.ATTACKER_DOMAIN}",
            "contacts": ["everyone"], "hq_country": "XX"}),
            "tool_calls": [{"id": "1", "type": "function", "function": {"name": "crm.send_email", "arguments": "{}"}}]}

    fake_llm.register(PURPOSE, obedient)
    profile, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=account_id, pages=pages,
                                                    run_id=run_id)
    assert profile.company == "Rocheval Transports S.A.S."  # the fallback from the site title, not model text
    assert profile.domain is None and profile.hq_country is None and profile.last_funding_eur is None
    assert profile.sources == (real,)
    assert [s.value for s in profile.growth_signals] == ["hiring"]
    assert meta.ignored_tool_calls == 1
    assert {"<extra:action>", "<extra:to>", "<extra:contacts>", "company", "domain"} <= set(meta.dropped_fields)
    dumped = profile.model_dump_json()
    assert synth_web.ATTACKER_DOMAIN not in dumped and "send_email" not in dumped


async def test_prompt_holds_page_text_only_no_crm_data(personas, web_pages, fake_llm, run_id):
    account_id = "A-0002"  # owned by sales_staff, rich CRM history
    pages = pages_for(account_id, web_pages)
    await extract_profile(personas["sales_staff"], account_id=account_id, pages=pages, run_id=run_id)
    prompt = json.dumps(_model_call(fake_llm)["messages"], ensure_ascii=False)
    crm_texts = [r["summary"] for r in owner_rows(
        "SELECT summary FROM crm.interactions WHERE account_id = %s", (account_id,))]
    crm_texts += [r["name"] for r in owner_rows("SELECT name FROM crm.contacts WHERE account_id = %s", (account_id,))]
    crm_texts += [r["name"] for r in owner_rows("SELECT name FROM crm.opportunities WHERE account_id = %s",
                                                (account_id,))]
    assert crm_texts and not [t for t in crm_texts if t in prompt]
    payload = json.loads(_model_call(fake_llm)["messages"][1]["content"])
    assert {p["url"] for p in payload["pages"]} <= {p["url"] for p in pages}


@pytest.mark.parametrize("who", ["hr_staff", "eng_head", "legal_staff", "platform_admin", "auditor"])
async def test_only_sales_and_ceo_may_research(personas, web_pages, fake_llm, run_id, who):
    with pytest.raises(NoPermission):
        await extract_profile(personas[who], account_id=FUNDED, pages=pages_for(FUNDED, web_pages), run_id=run_id)
    assert fake_llm.calls == []


@pytest.mark.parametrize("who", ["sales_head", "ceo"])
async def test_sales_head_and_ceo_may_research(personas, web_pages, fake_llm, run_id, who):
    profile = await extract_profile(personas[who], account_id=FUNDED, pages=pages_for(FUNDED, web_pages),
                                    run_id=run_id)
    assert profile.company == "Marevia Distribuzione S.r.l."


async def test_unpublished_and_foreign_pages_are_not_used(personas, web_pages, fake_llm, run_id):
    pages = pages_for(FUNDED, web_pages)
    future = {**pages[0], "url": "https://logistics-weekly.example/2027/future", "published_on": "2027-01-01"}
    other = pages_for("A-0004", web_pages)[0]
    _, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=FUNDED,
                                              pages=[*pages, future, other], run_id=run_id)
    assert set(meta.pages_used) == {p["url"] for p in pages}
    with pytest.raises(ValueError):
        await extract_profile(personas["sales_staff"], account_id="A-9999", pages=pages, run_id=run_id)


def test_fake_responder_is_registered_for_the_sales_prefix():
    from corpai.sales import fake as sales_fake

    fake = get_fake()
    assert fake.responder_for("sales.web_extract") is sales_fake.web_extract
    assert fake.responder_for("sales.strategy") is sales_fake.sales_default


async def test_public_only_needs_every_page_verified_as_published(personas, web_pages, fake_llm, run_id):
    """A caller cannot launder internal text through the public_only relaxation by calling it a page."""
    pages = pages_for(FUNDED, web_pages)
    tampered = [{**pages[0], "text": pages[0]["text"] + " Internal note: our discount floor for this deal is "
                                                          "EUR 40,000 on the 2027 renewal quote."}, *pages[1:]]
    _, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=FUNDED, pages=tampered,
                                              run_id=run_id)
    assert meta.public_only is False
    (row,) = _audit(run_id)
    assert row["details"]["public_only"] is False
    assert _model_call(fake_llm)["route"] == "local"  # the quote amount is commercial money: L3


async def test_pages_as_returned_by_web_fetch_are_accepted(personas, web_pages, fake_llm, run_id):
    """web.fetch returns url, domain, title, kind, published_on and text, without the account id."""
    fetched = [{k: p[k] for k in ("url", "domain", "title", "kind", "published_on", "text")}
               for p in pages_for(FUNDED, web_pages)]
    profile, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=FUNDED, pages=fetched,
                                                    run_id=run_id)
    assert meta.public_only is True and meta.route == "external"
    assert profile.company == "Marevia Distribuzione S.r.l."


_INTERNAL = "Internal note: our discount floor for this deal is EUR 40,000 on the 2027 renewal quote."


async def test_public_only_refuses_a_duplicate_url_with_internal_text(personas, web_pages, fake_llm, run_id):
    """Review finding: a tampered page placed before the genuine page with the same URL used to be
    hidden by a URL-keyed lookup (last page wins), so public_only stayed True and the quote went external."""
    pages = pages_for(FUNDED, web_pages)
    forged = {**pages[0], "text": _INTERNAL}
    _, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=FUNDED,
                                              pages=[forged, *pages], run_id=run_id)
    assert meta.public_only is False
    call = _model_call(fake_llm)
    assert _INTERNAL in message_text(call["messages"])  # it is in the prompt, so it must not go external
    assert call["route"] == "local"


@pytest.mark.parametrize("field", ["domain", "kind"])
async def test_public_only_checks_every_prompt_field(personas, web_pages, fake_llm, run_id, field):
    """domain and kind go into the prompt too; text placed there must also clear the published check."""
    pages = pages_for(FUNDED, web_pages)
    tampered = [{**pages[0], field: "renewal quote floor EUR 40,000 agreed"}, *pages[1:]]
    _, meta = await extract_profile_with_meta(personas["sales_staff"], account_id=FUNDED, pages=tampered,
                                              run_id=run_id)
    assert meta.public_only is False
    assert _model_call(fake_llm)["route"] == "local"
