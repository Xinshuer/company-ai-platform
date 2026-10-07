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

  // ---- the fake model's text is labelled as such
  const note = t("fake_model_note");
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

  out.t = {};
  for (const k of ["fake_model_note", "demo_no_recording_title", "demo_unrecorded_suffix", "metrics_no_periods",
                   "metrics_no_periods_cfo", "metrics_refresh", "err_demo_not_recorded", "hr_workspace_empty",
                   "route_external"]) {
    out.t[k] = t(k);
  }
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
