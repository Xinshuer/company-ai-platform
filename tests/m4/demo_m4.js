// Runs ui/js/demo.js plus the M4 demo extensions (demo_sales.js, demo_hr.js) under Node with the
// fixtures and prints one JSON object of scenario results (tests/m4/test_ui_m4.py). No browser.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.resolve(__dirname, "..", "..", "ui");
const store = {};
const sandbox = {
  console, setTimeout, clearTimeout, Promise, JSON, Math, Date,
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; }
  }
};
sandbox.window = sandbox;
vm.createContext(sandbox);
function load(rel) { vm.runInContext(fs.readFileSync(path.join(UI, rel), "utf8"), sandbox, {filename: rel}); }
load("js/fixtures_base.js");
for (const f of fs.readdirSync(path.join(UI, "fixtures")).filter((f) => f.endsWith(".js")).sort()) load("fixtures/" + f);
load("js/util.js");
load("js/demo.js");
for (const f of fs.readdirSync(path.join(UI, "js")).filter((f) => /^demo_[a-z]+\.js$/.test(f)).sort()) load("js/" + f);

const D = sandbox.KB.demo;
const F = sandbox.FIXTURES;
const out = {};

async function call(name, method, p, body) {
  try {
    out[name] = {ok: true, value: await D.request(method, p, body)};
  } catch (e) {
    out[name] = {ok: false, status: e.status, code: e.code};
  }
  return out[name];
}
function uid(key) { return F.me.by_persona[key].user_id; }
async function login(key) { return D.request("POST", "/api/auth/demo-login", {user_id: uid(key)}); }
async function sw(name, on) {
  await login("platform_admin");
  await D.request("POST", "/api/admin/switches", {name: name, on: on, reason: "drill"});
}
const run = (app, input, thread) => ({app: app, input: input, thread_id: thread || null, user_level: 1});

(async () => {
  // ---- sales
  await login("sales_staff");
  await call("followup_without_profile", "POST", "/api/runs", run("sales_profile", "/followup CT-00004"));
  const prof = await call("staff_profile", "POST", "/api/runs", run("sales_profile", "Havenmeer Distributie B.V."));
  await call("followup_in_thread", "POST", "/api/runs", run("sales_profile", "/followup CT-00004", prof.value.thread_id));
  await call("followup_other_contact", "POST", "/api/runs", run("sales_profile", "/followup CT-00006", prof.value.thread_id));
  await call("staff_elbstrom", "POST", "/api/runs", run("sales_profile", "Elbstrom Paketdienst GmbH"));
  await call("staff_accounts", "GET", "/api/sales/accounts");
  await login("eng_staff");
  await call("eng_accounts", "GET", "/api/sales/accounts");
  await call("eng_sales_run", "POST", "/api/runs", run("sales_profile", "Havenmeer Distributie B.V."));
  await sw("local_down", true);
  await login("sales_staff");
  await call("staff_profile_local_down", "POST", "/api/runs", run("sales_profile", "Havenmeer Distributie B.V."));
  await sw("local_down", false);

  // ---- hr screening and the decision
  await login("hr_staff");
  const scr = await call("screen", "POST", "/api/runs", run("hr_screening", "CAND-007"));
  const th = scr.value.thread_id;
  const dec = (body) => Object.assign({thread_id: th, candidate_id: "CAND-007", decision: "hold", reason: "x"}, body);
  await call("decide_no_reason", "POST", "/api/hr/screenings/decision", dec({reason: "  "}));
  await call("decide_bad_decision", "POST", "/api/hr/screenings/decision", dec({decision: "auto"}));
  await call("decide_wrong_candidate", "POST", "/api/hr/screenings/decision", dec({candidate_id: "CAND-001"}));
  await login("hr_head");
  await call("decide_other_persona", "POST", "/api/hr/screenings/decision", dec({reason: "fine"}));
  await login("eng_staff");
  await call("decide_not_hr", "POST", "/api/hr/screenings/decision", dec({reason: "fine"}));
  await login("hr_staff");
  await call("decide_ok", "POST", "/api/hr/screenings/decision", dec({reason: "check in the interview"}));
  await call("decide_twice", "POST", "/api/hr/screenings/decision", dec({reason: "again"}));
  await sw("local_down", true);
  await login("hr_staff");
  const down = await call("screen_local_down", "POST", "/api/runs", run("hr_screening", "CAND-001"));
  await call("decide_after_failed_run", "POST", "/api/hr/screenings/decision",
             {thread_id: down.value.thread_id, candidate_id: "CAND-001", decision: "hold", reason: "x"});
  await sw("local_down", false);

  // ---- candidates and résumés
  await login("sales_head");
  await call("sh_candidates", "GET", "/api/hr/candidates");
  await call("sh_resume_own", "GET", "/api/hr/candidates/CAND-005");
  await call("sh_resume_other", "GET", "/api/hr/candidates/CAND-001");
  await call("sh_req_other", "GET", "/api/hr/candidates?req_id=R-01");
  await call("sh_screening_app", "GET", "/api/hr/candidates?app=hr_screening");
  await login("eng_staff");
  await call("eng_candidates", "GET", "/api/hr/candidates");
  await login("auditor");
  await call("auditor_candidates", "GET", "/api/hr/candidates");

  // ---- headcount
  await login("hr_staff");
  await call("hc_staff", "POST", "/api/runs", run("hr_headcount", "anything"));
  await login("hr_head");
  await call("hc_head", "POST", "/api/runs", run("hr_headcount", "anything"));

  await login("auditor");
  await call("audit", "GET", "/api/audit?after_seq=0&limit=1000");
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
