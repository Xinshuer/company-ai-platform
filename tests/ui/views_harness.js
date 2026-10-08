// Renders the real pages (ui/js/view_*.js) in demo mode on a small fake DOM (tests/ui/fake_dom.js) and
// prints one JSON object of what a viewer would see; tests/ui/test_demo_views.py asserts on it.
// Covers what the demo backend tests cannot: badges, notes and controls drawn by the views.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const {Document} = require("./fake_dom.js");

const UI = path.resolve(__dirname, "..", "..", "ui");
const store = {};
const document = new Document();
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval, Promise, JSON, Math, Date, document,
  navigator: {language: "zh-CN", clipboard: null},
  location: {protocol: "file:", hash: "", href: "file:///ui/index.html"},
  matchMedia: () => ({matches: false, addEventListener() {}}),
  addEventListener() {},
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; }
  }
};
sandbox.window = sandbox;
vm.createContext(sandbox);
function load(rel) { vm.runInContext(fs.readFileSync(path.join(UI, rel), "utf8"), sandbox, {filename: rel}); }

// the page's script order (ui/index.html), with the fixtures loaded up front as demo mode does
load("js/fixtures_base.js");
for (const f of fs.readdirSync(path.join(UI, "fixtures")).filter((f) => f.endsWith(".js")).sort()) load("fixtures/" + f);
for (const f of ["js/fixtures_loader.js", "js/i18n.js", "js/util.js", "js/icons.js", "js/lang.js"]) load(f);
for (const f of fs.readdirSync(path.join(UI, "js")).filter((f) => /^i18n_[a-z]+\.js$/.test(f)).sort()) load("js/" + f);
load("js/demo.js");
for (const f of fs.readdirSync(path.join(UI, "js")).filter((f) => /^demo_[a-z]+\.js$/.test(f)).sort()) load("js/" + f);
load("js/api.js");
load("js/components.js");
for (const f of fs.readdirSync(path.join(UI, "js")).filter((f) => /^view_[a-z]+\.js$/.test(f)).sort()) load("js/" + f);

const KB = sandbox.KB;
const F = sandbox.FIXTURES;
// app.js and router.js drive navigation; the views only need these hooks
KB.state = {me: null, recentRuns: []};
KB.app = {rememberRun() {}, sessionLost() { throw new Error("session lost"); }, updateModeBadge() {}, refreshBanner() {},
          loadMe() {}, login() {}};
KB.router = {refresh() {}, render() {}};
const t = (k, v) => KB.lang.t(k, v);
const settle = (ms) => new Promise((r) => setTimeout(r, ms || 700));
const out = {};

function uid(key) { return F.me.by_persona[key].user_id; }
async function login(key) {
  await KB.demo.request("POST", "/api/auth/demo-login", {user_id: uid(key)});
  KB.state.me = await KB.demo.request("GET", "/api/me");
}
async function sw(name, on) {
  await login("platform_admin");
  await KB.demo.request("POST", "/api/admin/switches", {name: name, on: on, reason: "drill"});
}
async function page(view, args) {
  const root = document.createElement("main");
  KB.views[view].render(root, args);
  await settle();
  return root;
}
function texts(nodes) { return nodes.map((n) => n.textContent); }
function has(root, key) { return root.textContent.indexOf(t(key)) >= 0; }
function submit(root, value) {
  const input = root.querySelector("form input");
  input.value = value;
  root.querySelector("form").dispatchEvent({type: "submit", preventDefault() {}});
}
function routes(node) {
  return node.querySelectorAll(".route").map((b) => (b.classList.contains("route-local") ? "local" : "external"));
}
// the step chips of a sales profile: span.chip with a <strong> step name and a route badge
function stepChips(root) {
  return root.querySelectorAll("span.chip").filter((c) => c.firstChild && c.firstChild.tagName === "STRONG")
    .map((c) => ({text: c.textContent, routes: routes(c)}));
}
function noteHeader(root) {
  const h = root.querySelectorAll("h3.label").filter((n) => n.textContent.indexOf(t("hr_note_title")) === 0)[0];
  return h ? {text: h.textContent, routes: routes(h)} : null;
}

(async () => {
  // ---- external_down: badges inside the run follow the run to local (review: step and note badges)
  for (const down of [false, true]) {
    await sw("external_down", down);
    const tag = down ? "down" : "up";
    await login("sales_staff");
    let root = await page("sales");
    submit(root, "Elbstrom Paketdienst GmbH");
    await settle(900);
    out["sales_steps_" + tag] = stepChips(root);
    out["sales_head_route_" + tag] = routes(root.querySelector(".run-head"));
    await login("hr_staff");
    root = await page("headcount");
    root.querySelector(".row-actions button").click();
    await settle(900);
    out["hc_note_" + tag] = noteHeader(root);
    out["hc_head_route_" + tag] = routes(root.querySelector(".run-head"));
    out["hc_text_" + tag] = root.querySelector(".answer").textContent;
  }
  await sw("external_down", false);

  // ---- recorded model text says it is a recording of the real model
  const note = t("demo_recorded_note");
  await login("sales_staff");
  let root = await page("sales");
  submit(root, "Havenmeer Distributie B.V.");
  await settle(900);
  out.sales_fake_note = root.textContent.indexOf(note) >= 0;
  // ---- the follow-up card: the recorded contact first and selected, the others disabled
  const sel = root.querySelectorAll("select").filter((s) => s.querySelector("option"))[0];
  out.followup_value = sel ? sel.value : null;
  out.followup_options = sel ? sel.options.map((o) => ({value: o.value, disabled: o.disabled, text: o.textContent})) : [];
  // ---- the sales form offers the recorded companies, marks the unrecorded accounts
  out.sales_chips = texts(root.querySelectorAll("[data-recorded] button"));
  out.sales_datalist = root.querySelector("datalist").options.map((o) => ({value: o.value, unrecorded: o.hasAttribute("data-unrecorded")}));

  await login("ceo");
  root = await page("report");
  submit(root, "2026-Q3 管理层报告");
  await settle(900);
  out.report_fake_note = root.textContent.indexOf(note) >= 0;
  await login("hr_staff");
  root = await page("headcount");
  root.querySelector(".row-actions button").click();
  await settle(900);
  out.hc_fake_note = root.textContent.indexOf(note) >= 0;

  // ---- personas without a recording are told up front; personas with one are not
  out.coverage = {};
  for (const [persona, view] of [["legal_head", "legal"], ["ceo", "legal"], ["legal_staff", "legal"],
                                 ["sales_head", "sales"], ["sales_staff", "sales"], ["ceo", "sales"],
                                 ["legal_head", "report"], ["hr_head", "report"], ["ceo", "report"],
                                 ["hr_head", "hr"], ["hr_staff", "hr"]]) {
    await login(persona);
    root = await page(view);
    out.coverage[persona + "@" + view] = has(root, "demo_no_recording_title");
  }
  // every persona who may start these apps has recordings: take one persona's away to see the note
  const saved = {legal: F.legal.runs, legalNoModel: F.legal.no_model_runs, sales: F.sales.runs};
  F.legal.runs = saved.legal.filter((it) => it.persona_key !== "legal_head");
  F.legal.no_model_runs = saved.legalNoModel.filter((it) => it.persona_key !== "legal_head");
  F.sales.runs = saved.sales.filter((it) => it.persona_key !== "sales_head");
  for (const [persona, view] of [["legal_head", "legal"], ["sales_head", "sales"], ["legal_staff", "legal"]]) {
    await login(persona);
    root = await page(view);
    out.coverage[persona + "@" + view + "-unrecorded"] = has(root, "demo_no_recording_title");
  }
  F.legal.runs = saved.legal;
  F.legal.no_model_runs = saved.legalNoModel;
  F.sales.runs = saved.sales;

  // ---- screening buttons: enabled exactly where a recording exists
  out.screen_buttons = {};
  for (const persona of ["hr_staff", "hr_head"]) {
    await login(persona);
    root = await page("hr");
    const rows = root.querySelectorAll("tr").filter((tr) => tr.querySelector("button"));
    out.screen_buttons[persona] = rows.map((tr) => {
      const b = tr.querySelector("button");
      const id = tr.querySelector("code").textContent;
      return {id: id, disabled: b.disabled, recorded: KB.demo.hasRecording("hr_screening", id)};
    });
  }
  // a disabled button does nothing; an enabled one gets a screening, never "no recording"
  await login("hr_head");
  root = await page("hr");
  const btns = root.querySelectorAll("tr").filter((tr) => tr.querySelector("button")).map((tr) => tr.querySelector("button"));
  const ws = () => root.querySelector(".result-slot[aria-live]");
  btns.filter((b) => b.disabled)[0].click();
  await settle(900);
  out.disabled_click_text = ws().textContent;
  btns.filter((b) => !b.disabled)[0].click();
  await settle(900);
  out.enabled_click_error = ws().textContent.indexOf(t("err_demo_not_recorded")) >= 0;

  // ---- legal: only contracts with a recorded review can be picked
  await login("legal_staff");
  root = await page("legal");
  const pick = root.querySelectorAll("select")[0];
  out.legal_pick_value = pick.value;
  out.legal_pick_enabled = pick.options.filter((o) => !o.disabled).map((o) => o.value);
  out.legal_pick_n = pick.options.length;

  // ---- metrics: the CFO can compute a first period when none exists yet (stubbed API)
  const realRequest = KB.api.request;
  const calls = [];
  let periods = [];
  KB.api.request = function (method, p, body) {
    calls.push([method, p, body || null]);
    if (method === "GET" && p === "/api/metrics") {
      return Promise.resolve({metrics: [], periods: periods.slice(),
        can: {sign: true, publish: true, refresh: true, drafts: true}, refreshable: ["2026-09", "2026-08", "2026-Q3"]});
    }
    if (method === "POST" && p === "/api/metrics/refresh") {
      periods = [body.period];
      return Promise.resolve({period: body.period, rows: 3});
    }
    if (method === "GET" && p.indexOf("/api/metrics/board") === 0) {
      return Promise.resolve({period: "2026-08", can: {sign: true, publish: true}, metrics: []});
    }
    return Promise.reject(new Error("unexpected " + method + " " + p));
  };
  KB.api.get = (p) => KB.api.request("GET", p);
  KB.api.post = (p, b) => KB.api.request("POST", p, b);
  await login("finance_head");
  root = await page("metrics");
  out.cfo_empty_text = root.textContent;
  const rsel = root.querySelectorAll("select")[0];
  out.cfo_refresh_options = rsel ? rsel.options.map((o) => o.value) : [];
  rsel.value = "2026-08";
  const rbtn = root.querySelectorAll("button").filter((b) => b.textContent.indexOf(t("metrics_refresh")) >= 0)[0];
  out.cfo_has_refresh_button = !!rbtn;
  rbtn.click();
  await settle(300);
  out.cfo_calls = calls.map((c) => c[0] + " " + c[1] + (c[2] ? " " + JSON.stringify(c[2]) : ""));
  out.cfo_after_text = root.textContent;
  // someone who may not refresh sees the plain "no periods" note and no refresh control
  KB.api.request = function (method, p) {
    if (method === "GET" && p === "/api/metrics") {
      return Promise.resolve({metrics: [], periods: [], can: {sign: false, publish: false, refresh: false, drafts: true}, refreshable: []});
    }
    return Promise.reject(new Error("unexpected " + p));
  };
  await login("finance_staff");
  root = await page("metrics");
  out.staff_empty_text = root.textContent;
  out.staff_selects = root.querySelectorAll("select").length;
  KB.api.request = realRequest;

  // ---- low UI findings of the wiring review (issues-log WR-12 .. WR-19)
  // focus is observable: the fake DOM's focus() records the element
  Object.getPrototypeOf(document.createElement("x")).focus = function () { document.activeElement = this; };
  const alerts = (node) => node.querySelectorAll("[role=alert]");

  // WR-19: the CEO is refused every requisition: one alert, listing all of them
  await login("ceo");
  root = await page("candidates");
  out.ceo_cand_alerts = alerts(root).map((n) => n.textContent);
  out.ceo_cand_cards = root.querySelectorAll(".card.pad").length;

  // WR-12: an own requisition nobody applied to yet is empty, not "no permission" (stubbed API)
  KB.api.request = function (method, p) {
    if (method === "GET" && p === "/api/hr/candidates?app=hr_candidates") {
      return Promise.resolve({app: "hr_candidates", own: ["R-03"], listed: [], requisitions: [
        {req_id: "R-03", title: "Account Executive", status: "open", own: true, candidates: [], denied: true},
        {req_id: "R-02", title: "Platform Engineer", status: "open", own: false, candidates: [], denied: true}],
        denied: [{id: "hr.requisition:R-03", reason: "not found or no permission"},
                 {id: "hr.requisition:R-02", reason: "not found or no permission"}]});
    }
    return Promise.reject(new Error("unexpected " + method + " " + p));
  };
  await login("sales_head");
  root = await page("candidates");
  out.empty_req = {alerts: alerts(root).length, text: root.textContent};
  KB.api.request = realRequest;

  // WR-17 and WR-14: the hiring manager's résumé view carries the bias notice; sources are translated
  root = await page("candidates");
  const candRows = () => root.querySelectorAll("tr").filter((tr) => tr.querySelector("button"));
  out.hm_sources = candRows().map((tr) => tr.querySelectorAll("td")[4].textContent);
  candRows()[0].querySelector("button").click();
  await settle();
  out.hm_resume_text = root.textContent;
  await login("hr_head");
  root = await page("hr");
  out.hr_page_has_hm_note = root.textContent.indexOf(t("hr_resume_bias_hm")) >= 0;

  // WR-18: the refused intent step shows the refusal, not L3; the profile's codes are translated
  await login("sales_staff");
  for (const company of ["Elbstrom Paketdienst GmbH", "Havenmeer Distributie B.V."]) {
    root = await page("sales");
    submit(root, company);
    await settle(900);
    const chip = stepChips(root).filter((c) => c.text.indexOf(t("sales_step_intent")) === 0)[0];
    out["intent_chip_" + company.split(" ")[0]] = chip ? chip.text : null;
    out["sales_text_" + company.split(" ")[0]] = root.textContent;
  }

  // WR-13: tabs with complete ARIA, arrow keys move focus, a click keeps focus on the selected tab
  await login("legal_staff");
  KB.state.legalTab = null;
  root = await page("legal");
  document.body.appendChild(root);  // so the re-rendered tab can be found by id
  const realRouter = KB.router;
  KB.router = {render() {}, refresh() { KB.util.clear(root); KB.views.legal.render(root); }};
  const tabs = () => root.querySelectorAll("[role=tab]");
  const panel = root.querySelector("[role=tabpanel]");
  out.tabs = tabs().map((b) => ({id: b.id, selected: b.getAttribute("aria-selected"), tabindex: b.getAttribute("tabindex"),
                                 controls: b.getAttribute("aria-controls")}));
  out.tabpanel = {id: panel.id, labelledby: panel.getAttribute("aria-labelledby")};
  const key = (b, k) => {
    const ev = {type: "keydown", key: k, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
    document.activeElement = null;
    b.dispatchEvent(ev);
    return {focused: document.activeElement ? document.activeElement.id : null, prevented: ev.defaultPrevented};
  };
  out.tab_keys = {right: key(tabs()[0], "ArrowRight"), left: key(tabs()[0], "ArrowLeft"), end: key(tabs()[1], "End"),
                  home: key(tabs()[2], "Home"), other: key(tabs()[0], "a")};
  // the contract picker names type and status in the page's language
  out.legal_option = root.querySelectorAll("select")[0].options.map((o) => o.textContent)
    .filter((s) => s.indexOf("C-0003") === 0)[0] || null;
  tabs()[2].click();  // "due"
  await settle();
  const due = tabs().filter((b) => b.getAttribute("aria-selected") === "true")[0];
  out.after_click = {selected: due.id, focused: document.activeElement === due, state: KB.state.legalTab};
  root.querySelector("form").dispatchEvent({type: "submit", preventDefault() {}});
  await settle(900);
  const dueRow = root.querySelectorAll("tr").filter((tr) => tr.textContent.indexOf("C-0003") >= 0)[0];
  out.due_cells = dueRow ? dueRow.querySelectorAll("td").map((td) => td.textContent) : null;
  tabs()[3].click();  // saved reviews: the requester by name
  await settle();
  out.reviews_text = root.textContent;
  KB.router = realRouter;
  KB.state.legalTab = null;
  document.body.removeChild(root);

  // #/metrics: not in the platform roles' navigation; the home card is locked for them
  load("js/router.js");
  const navLinks = () => document.getElementById("sidenav").querySelectorAll("a").map((a) => a.getAttribute("href"));
  out.nav = {};
  for (const persona of ["platform_admin", "auditor", "ceo", "sales_staff"]) {
    await login(persona);
    KB.router.buildNav();
    root = document.createElement("main");
    KB.views.home.render(root);
    out.nav[persona] = {metrics_link: navLinks().indexOf("#/metrics") >= 0,
                        home_card: root.querySelectorAll("a").some((a) => a.getAttribute("href") === "#/metrics")};
  }

  // WR-14: a failed refresh names the error in the page's language, the server's words beside it
  KB.api.request = function (method, p) {
    if (method === "GET" && p === "/api/metrics") {
      return Promise.resolve({metrics: [], periods: [], can: {sign: true, publish: true, refresh: true, drafts: true},
                              refreshable: ["2026-09"]});
    }
    if (method === "POST" && p === "/api/metrics/refresh") {
      const e = new Error("period 2026-09 has no ledger rows yet");
      Object.assign(e, {status: 400, code: "bad_request", serverMessage: "period 2026-09 has no ledger rows yet"});
      return Promise.reject(e);
    }
    return Promise.reject(new Error("unexpected " + method + " " + p));
  };
  await login("finance_head");
  root = await page("metrics");
  root.querySelectorAll("button").filter((b) => b.textContent.indexOf(t("metrics_refresh")) >= 0)[0].click();
  await settle(300);
  out.refresh_fail = {toast: document.getElementById("toast").textContent, text: root.textContent};
  KB.api.request = realRequest;

  out.t = {};
  for (const k of ["demo_recorded_note", "demo_no_recording_title", "demo_unrecorded_suffix", "metrics_no_periods",
                   "metrics_no_periods_cfo", "metrics_refresh", "err_demo_not_recorded", "hr_workspace_empty",
                   "route_external", "hr_req_empty", "hr_req_unseen", "denied_title", "hr_resume_bias_hm",
                   "sales_step_refused", "sales_ind_parcel_delivery", "sales_ind_retail_distribution",
                   "sales_sig_fleet_expansion", "legal_ctype_order_form", "legal_cstatus_signed", "err_bad_request"]) {
    out.t[k] = t(k);
  }
  out.t.due_action = t("legal_action_auto_renew", {d: "2026-10-16"});
  out.t.sources = ["careers_page", "job_board", "referral", "agency"].map((s) => t("hr_source_" + s));
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
