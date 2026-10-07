// Runs ui/js/demo.js under Node with the fixtures and prints one JSON object of scenario results.
// Used by tests/ui/test_demo_backend.py; no browser, no network.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.resolve(__dirname, "..", "..", "ui");
const store = {};
const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  Promise,
  JSON,
  Math,
  Date,
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

const D = sandbox.KB.demo;
const F = sandbox.FIXTURES;
const out = {};

async function call(name, method, p, body) {
  try {
    const v = await D.request(method, p, body);
    out[name] = {ok: true, value: v};
  } catch (e) {
    out[name] = {ok: false, status: e.status, code: e.code};
  }
  return out[name];
}
function uid(key) { return F.me.by_persona[key].user_id; }
async function login(key) { return D.request("POST", "/api/auth/demo-login", {user_id: uid(key)}); }

(async () => {
  const Q = F.compare.question;
  await call("me_before_login", "GET", "/api/me");
  await call("runs_before_login", "POST", "/api/runs", {app: "qa", input: Q});

  await login("sales_staff");
  await call("sales_qa", "POST", "/api/runs", {app: "qa", input: Q, thread_id: null, user_level: 1});
  await call("sales_legal_app", "POST", "/api/runs", {app: "legal_review", input: "x"});
  await call("sales_audit", "GET", "/api/audit?after_seq=0&limit=200");
  await call("sales_admin_get", "GET", "/api/admin/switches");
  await call("sales_admin_post", "POST", "/api/admin/switches", {name: "local_down", on: true, reason: "x"});
  await call("sales_to_approve", "GET", "/api/proposals?box=to_approve");
  const mine = await call("sales_mine", "GET", "/api/proposals?box=mine");
  const pend3 = mine.value.find((p) => p.tier === 3 && p.status === "pending");
  await call("self_approve_tier3", "POST", "/api/proposals/" + pend3.proposal_id + "/approve",
             {params_hash: pend3.params_hash, reason: "looks fine"});
  await call("own_trace", "GET", "/api/runs/" + out.sales_qa.value.run_id + "/trace");
  const ceoRun = F.runs.items.find((i) => i.persona_key === "ceo").run.run_id;
  await call("other_trace", "GET", "/api/runs/" + ceoRun + "/trace");
  await call("public_raised", "POST", "/api/runs", {app: "qa", input: F.runs.items.find((i) => i.persona_key === "*" && i.app === "qa").input, user_level: 3});
  await call("public_lowered", "POST", "/api/runs", {app: "qa", input: Q, user_level: 1});
  // a thread keeps its level: a public question in a thread that already reached L3 stays local
  const PUB = F.runs.items.find((i) => i.persona_key === "*" && i.app === "qa").input;
  const thr = out.sales_qa.value.thread_id;
  await call("thread_public_after_l3", "POST", "/api/runs", {app: "qa", input: PUB, thread_id: thr, user_level: 1});
  await call("fresh_public", "POST", "/api/runs", {app: "qa", input: PUB, thread_id: null, user_level: 1});
  out.has_trace_own = D.hasTrace(out.sales_qa.value.run_id);
  out.has_trace_other = D.hasTrace(F.runs.items.find((i) => i.persona_key === "ceo").run.run_id);
  out.has_trace_unrecorded = D.hasTrace(F.runs.items.find((i) => i.app === "sales_profile").run.run_id);
  out.sample_sales_staff = D.sample("sales_profile", "sales_staff");
  out.sample_sales_head = D.sample("sales_profile", "sales_head");

  await login("sales_head");
  await call("head_to_approve", "GET", "/api/proposals?box=to_approve");
  await call("approve_no_reason", "POST", "/api/proposals/" + pend3.proposal_id + "/approve",
             {params_hash: pend3.params_hash, reason: "  "});
  await call("approve_bad_hash", "POST", "/api/proposals/" + pend3.proposal_id + "/approve",
             {params_hash: "0".repeat(64), reason: "ok"});
  await call("approve_ok", "POST", "/api/proposals/" + pend3.proposal_id + "/approve",
             {params_hash: pend3.params_hash, reason: "wording checked"});
  await call("approve_twice", "POST", "/api/proposals/" + pend3.proposal_id + "/approve",
             {params_hash: pend3.params_hash, reason: "again"});
  await call("head_to_approve_after", "GET", "/api/proposals?box=to_approve");

  await login("platform_admin");
  await call("admin_no_reason", "POST", "/api/admin/switches", {name: "local_down", on: true, reason: ""});
  await call("admin_bad_name", "POST", "/api/admin/switches", {name: "everything_down", on: true, reason: "x"});
  await call("admin_local_down", "POST", "/api/admin/switches", {name: "local_down", on: true, reason: "drill"});
  await call("admin_audit", "GET", "/api/audit?after_seq=0&limit=200");

  await login("sales_staff");
  await call("sales_qa_local_down", "POST", "/api/runs", {app: "qa", input: Q, user_level: 1});
  await login("ceo");
  await call("ceo_qa_local_down", "POST", "/api/runs", {app: "qa", input: Q, user_level: 1});
  await login("eng_staff");
  await call("eng_qa_local_down", "POST", "/api/runs", {app: "qa", input: Q, user_level: 1});
  // raised by the user to L3/L4: needs the local route, which is down -> fails, never answers
  await call("eng_public_raised_local_down", "POST", "/api/runs", {app: "qa", input: PUB, user_level: 3});
  await call("eng_q_raised_local_down", "POST", "/api/runs", {app: "qa", input: Q, user_level: 4});

  await login("platform_admin");
  await D.request("POST", "/api/admin/switches", {name: "local_down", on: false, reason: "drill over"});
  await call("admin_external_down", "POST", "/api/admin/switches", {name: "external_down", on: true, reason: "drill"});
  await login("eng_staff");
  await call("eng_qa_external_down", "POST", "/api/runs", {app: "qa", input: Q, user_level: 1});

  await login("auditor");
  await call("auditor_audit", "GET", "/api/audit?after_seq=0&limit=1000");
  await call("auditor_admin", "GET", "/api/admin/switches");

  await call("logout", "POST", "/api/auth/logout", {});
  await call("me_after_logout", "GET", "/api/me");

  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
