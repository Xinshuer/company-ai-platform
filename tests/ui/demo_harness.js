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
// the milestone demo extensions, in page order (ui/index.html loads them right after demo.js)
for (const f of fs.readdirSync(path.join(UI, "js")).filter((f) => /^demo_[a-z]+\.js$/.test(f)).sort()) load("js/" + f);

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
  // one of this persona's own recordings whose trace was not recorded
  const untraced = F.sales.runs.find((i) => i.persona_key === "sales_staff" && !(F.sales.traces || {})[i.run.run_id]);
  out.has_trace_unrecorded = D.hasTrace(untraced.run.run_id);
  out.sample_sales_staff = D.sample("sales_profile", "sales_staff");
  out.sample_sales_head = D.sample("sales_profile", "sales_head");
  out.sample_no_recording = D.sample("sales_profile", "eng_staff");  // someone else's sample, not runnable

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

  // ---- milestone extension points (KB.demo.register): the shared rules still apply ----------------
  out.registered = D.registered().slice().sort();
  const pub = F.runs.items.find((i) => i.persona_key === "*" && i.app === "qa");
  // a recording the extension supplies for legal_review: external, below L3, as recorded
  const extItem = JSON.parse(JSON.stringify(pub));
  extItem.app = "legal_review";
  extItem.run.app = "legal_review";
  extItem.run.run_id = "run_w0ext";
  extItem.input = "ext question";
  const wrongApp = JSON.parse(JSON.stringify(pub));  // a recording for another app
  wrongApp.input = "wrong app";
  D.register("w0test", {
    apps: ["legal_review"],
    findRun: (ctx, key, body) => body.input === "ext question" ? extItem
      : body.input === "wrong app" ? wrongApp : null,
    init: (ctx) => { ctx.state.inits = (ctx.state.inits || 0) + 1; },
    routes: [
      {method: "GET", path: /^\/api\/w0test\/ping$/, handle: (ctx, req) => ({persona: req.persona, me: req.me.user_id})},
      {method: "GET", path: /^\/api\/me$/, handle: () => ({shadowed: true})},
      {method: "POST", path: /^\/api\/w0test\/run$/, handle: (ctx, req) => ctx.finishRun(req.body, extItem)}
    ]
  });
  await call("ext_dup_register", "GET", "/api/health");
  try { D.register("w0test", {}); out.ext_dup = "accepted"; } catch (e) { out.ext_dup = "refused"; }

  await call("logout_for_ext", "POST", "/api/auth/logout", {});
  await call("ext_route_no_persona", "GET", "/api/w0test/ping");
  await login("platform_admin");
  await D.request("POST", "/api/admin/switches", {name: "external_down", on: false, reason: "drill over"});
  await login("legal_staff");
  await call("ext_route", "GET", "/api/w0test/ping");
  await call("ext_cannot_shadow", "GET", "/api/me");
  await call("ext_run_plain", "POST", "/api/runs", {app: "legal_review", input: "ext question", thread_id: null, user_level: 1});
  await call("ext_run_raised", "POST", "/api/runs", {app: "legal_review", input: "ext question", thread_id: "th_w0", user_level: 3});
  await call("ext_run_thread", "POST", "/api/runs", {app: "legal_review", input: "ext question", thread_id: "th_w0", user_level: 1});
  await call("ext_wrong_app", "POST", "/api/runs", {app: "legal_review", input: "wrong app", user_level: 1});
  await call("ext_route_run", "POST", "/api/w0test/run", {app: "legal_review", input: "x", thread_id: null, user_level: 3});
  await login("platform_admin");
  await D.request("POST", "/api/admin/switches", {name: "external_down", on: true, reason: "drill"});
  await login("legal_staff");
  await call("ext_run_external_down", "POST", "/api/runs", {app: "legal_review", input: "ext question", user_level: 1});
  await login("platform_admin");
  await D.request("POST", "/api/admin/switches", {name: "external_down", on: false, reason: "drill over"});
  await D.request("POST", "/api/admin/switches", {name: "local_down", on: true, reason: "drill"});
  await login("legal_staff");
  await call("ext_run_local_down", "POST", "/api/runs", {app: "legal_review", input: "ext question", user_level: 3});
  await login("platform_admin");
  await D.request("POST", "/api/admin/switches", {name: "local_down", on: false, reason: "drill over"});
  await login("sales_staff");  // may not start legal_review: neither through /api/runs nor an extension route
  await call("ext_run_forbidden", "POST", "/api/runs", {app: "legal_review", input: "ext question", user_level: 1});
  await call("ext_route_run_forbidden", "POST", "/api/w0test/run", {app: "legal_review", input: "x", user_level: 1});

  // milestone fixture files: their runs, traces and proposals join the shared ones on reload
  F.legal.runs.push({persona_key: "legal_staff", app: "legal_review", input: "fixture question",
                     run: Object.assign(JSON.parse(JSON.stringify(extItem.run)), {run_id: "run_w0fix"})});
  F.legal.traces.run_w0fix = JSON.parse(JSON.stringify(Object.values(F.traces.by_run)[0]));
  F.legal.traces.run_w0fix.run_id = "run_w0fix";
  // a shared ("*") recording that carries a trace: nobody's own run, so the trace is never served (SEC-1)
  F.legal.runs.push({persona_key: "*", app: "legal_review", input: "shared fixture question",
                     run: Object.assign(JSON.parse(JSON.stringify(extItem.run)), {run_id: "run_w0shared"})});
  F.legal.traces.run_w0shared = JSON.parse(JSON.stringify(Object.values(F.traces.by_run)[0]));
  F.legal.traces.run_w0shared.run_id = "run_w0shared";
  D.reload();
  out.ext_inits_after_reload = D.registered().indexOf("w0test") >= 0;
  await login("legal_staff");
  await call("fixture_run", "POST", "/api/runs", {app: "legal_review", input: "fixture question", user_level: 1});
  out.fixture_trace_own = D.hasTrace("run_w0fix");
  await call("shared_run", "POST", "/api/runs", {app: "legal_review", input: "shared fixture question", user_level: 1});
  out.shared_trace_link = D.hasTrace("run_w0shared");
  await call("shared_trace", "GET", "/api/runs/run_w0shared/trace");
  await login("legal_head");
  out.fixture_trace_other = D.hasTrace("run_w0fix");

  await call("logout", "POST", "/api/auth/logout", {});
  await call("me_after_logout", "GET", "/api/me");

  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
