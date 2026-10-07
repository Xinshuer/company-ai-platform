// Runs ui/js/demo.js + ui/js/demo_legal.js under Node with the fixtures and prints one JSON object of
// scenario results for tests/m2/test_m2_ui.py. No browser, no network.
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
function run(input, extra) { return Object.assign({app: "legal_review", input: input, thread_id: null, user_level: 1}, extra || {}); }
const J = (o) => JSON.stringify(o);
async function sw(name, on) {
  await login("platform_admin");
  await D.request("POST", "/api/admin/switches", {name: name, on: on, reason: "harness"});
}

(async () => {
  await login("legal_staff");
  await call("review_c4", "POST", "/api/runs", run(J({mode: "review", contract_id: "C-0004"})));
  await call("review_c4_plain", "POST", "/api/runs", run("review C-0004"));
  await call("review_c4_keys_reordered", "POST", "/api/runs", run('{"contract_id": "C-0004", "mode": "review"}'));
  await call("review_c5", "POST", "/api/runs", run(J({mode: "review", contract_id: "C-0005"})));
  await call("review_unrecorded", "POST", "/api/runs", run(J({mode: "review", contract_id: "C-0010"})));
  await call("due_default", "POST", "/api/runs", run(J({mode: "due"})));
  await call("due_plain", "POST", "/api/runs", run("下个月到期的合同"));
  await call("samples", "GET", "/api/legal/samples");
  const reqs = out.samples.ok ? out.samples.value.requests : [];
  const send = reqs.find((r) => r.mode === "send");
  const draft = reqs.find((r) => r.mode === "draft");
  await call("send", "POST", "/api/runs", run(J(send)));
  await call("send_other_recipient", "POST", "/api/runs", run(J(Object.assign({}, send, {to: "signature@contract-desk.example"}))));
  await call("draft", "POST", "/api/runs", run(J(draft)));
  await call("contracts", "GET", "/api/legal/contracts");
  await call("reviews", "GET", "/api/legal/reviews");
  const rid = out.review_c4.ok ? out.review_c4.value.output.data.review.review_id : "rev_x";
  out.rid = rid;
  await call("review_detail", "GET", "/api/legal/reviews/" + rid);
  await call("review_missing", "GET", "/api/legal/reviews/rev_01nonexistent0000000");
  await call("upload", "POST", "/api/legal/uploads", {text: "1. Services. x", filename: "a.txt"});
  await call("review_upload", "POST", "/api/runs", run(J({mode: "review", upload_id: out.upload.ok ? out.upload.value.upload_id : "x"})));
  await call("mine", "GET", "/api/proposals?box=mine");
  const pid = out.send.ok && out.send.value.pending_approval ? out.send.value.pending_approval.proposal_id : "x";
  out.pid = pid;
  const mineSend = out.mine.ok ? out.mine.value.find((p) => p.proposal_id === pid) : null;
  await call("self_approve", "POST", "/api/proposals/" + pid + "/approve",
             {params_hash: mineSend ? mineSend.params_hash : "", reason: "looks fine"});

  await login("sales_staff");
  await call("sales_run", "POST", "/api/runs", run(J({mode: "review", contract_id: "C-0004"})));
  await call("sales_contracts", "GET", "/api/legal/contracts");
  await call("sales_reviews", "GET", "/api/legal/reviews");
  await call("sales_review_detail", "GET", "/api/legal/reviews/" + rid);
  await call("sales_upload", "POST", "/api/legal/uploads", {text: "x"});
  await call("sales_samples", "GET", "/api/legal/samples");

  await login("ceo");
  await call("ceo_review_detail", "GET", "/api/legal/reviews/" + rid);
  await call("ceo_contracts", "GET", "/api/legal/contracts");

  await login("legal_head");
  await call("head_inbox", "GET", "/api/proposals?box=to_approve");
  const headSend = out.head_inbox.ok ? out.head_inbox.value.find((p) => p.proposal_id === pid) : null;
  await call("head_review_detail", "GET", "/api/legal/reviews/" + rid);
  await call("head_approve", "POST", "/api/proposals/" + pid + "/approve",
             {params_hash: headSend ? headSend.params_hash : "", reason: "Checked the redlines."});

  await sw("local_down", true);
  await login("legal_staff");
  await call("review_local_down", "POST", "/api/runs", run(J({mode: "review", contract_id: "C-0004"})));
  await call("due_local_down", "POST", "/api/runs", run(J({mode: "due", month: "2026-11"})));
  await sw("local_down", false);

  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
