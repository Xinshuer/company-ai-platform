// Runs ui/js/demo.js plus the milestone demo files under Node with the fixtures and prints one JSON
// object of M3 scenario results (report runs, export, metric board, sign-off). Used by
// tests/m3/test_ui.py; no browser, no network.
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
const RUN = {app: "report", thread_id: null, user_level: 1};

(async () => {
  await call("metrics_before_login", "GET", "/api/metrics");

  // ---- report runs
  await login("ceo");
  const ceo = await call("ceo_run", "POST", "/api/runs", Object.assign({input: "2026-Q3 管理层报告"}, RUN));
  await call("ceo_run_en", "POST", "/api/runs", Object.assign({input: "2026-Q3 management report"}, RUN));
  await call("ceo_run_q_first", "POST", "/api/runs", Object.assign({input: "Q3 2026 report"}, RUN));
  await call("ceo_run_zh_quarter", "POST", "/api/runs", Object.assign({input: "2026年第三季度"}, RUN));
  await call("ceo_run_unrecorded_period", "POST", "/api/runs", Object.assign({input: "2026-Q1 管理层报告"}, RUN));
  await call("ceo_run_raised", "POST", "/api/runs", Object.assign({input: "2026-Q3 管理层报告"}, RUN, {user_level: 4}));
  const ceoRun = ceo.value;
  await call("ceo_export", "POST", "/api/reports/" + ceoRun.run_id + "/export", {thread_id: ceoRun.thread_id});
  await call("ceo_export_no_thread", "POST", "/api/reports/" + ceoRun.run_id + "/export", {});

  await login("eng_head");
  const cto = await call("cto_run", "POST", "/api/runs", Object.assign({input: "2026-Q3 管理层报告"}, RUN));
  await call("cto_exports_ceo_run", "POST", "/api/reports/" + ceoRun.run_id + "/export", {thread_id: ceoRun.thread_id});
  await call("cto_export", "POST", "/api/reports/" + cto.value.run_id + "/export", {thread_id: cto.value.thread_id});

  await login("sales_staff");
  await call("sales_staff_run", "POST", "/api/runs", Object.assign({input: "2026-Q3 管理层报告"}, RUN));

  await login("platform_admin");
  await call("admin_metrics", "GET", "/api/metrics");
  await call("admin_local_down", "POST", "/api/admin/switches", {name: "local_down", on: true, reason: "drill"});
  await login("ceo");
  await call("ceo_run_local_down", "POST", "/api/runs", Object.assign({input: "2026-Q3 管理层报告"}, RUN));
  await login("platform_admin");
  await call("admin_local_up", "POST", "/api/admin/switches", {name: "local_down", on: false, reason: "drill over"});

  // ---- metric board and sign-off
  await login("auditor");
  await call("auditor_board", "GET", "/api/metrics/board?period=2026-Q3");
  await login("sales_staff");
  await call("sales_list", "GET", "/api/metrics");
  await call("sales_board_q3", "GET", "/api/metrics/board?period=2026-Q3");
  await call("sales_board_09_before", "GET", "/api/metrics/board?period=2026-09");
  await call("sales_sign", "POST", "/api/metrics/sign", {period: "2026-09", metric_ids: ["arr"]});
  await login("finance_staff");
  await call("fs_list", "GET", "/api/metrics");
  await call("fs_board_09", "GET", "/api/metrics/board?period=2026-09");
  await call("fs_sign", "POST", "/api/metrics/sign", {period: "2026-09", metric_ids: ["arr"]});
  await call("fs_refresh", "POST", "/api/metrics/refresh", {period: "2026-09"});
  await login("ceo");
  await call("ceo_list", "GET", "/api/metrics");
  await call("ceo_publish", "POST", "/api/metrics/publish", {period: "2026-09", metric_ids: ["arr"]});
  await login("finance_head");
  await call("cfo_list", "GET", "/api/metrics");
  await call("cfo_publish_early", "POST", "/api/metrics/publish", {period: "2026-09", metric_ids: ["arr"]});
  await call("cfo_sign", "POST", "/api/metrics/sign", {period: "2026-09", metric_ids: ["arr", "headcount"]});
  await call("cfo_publish", "POST", "/api/metrics/publish", {period: "2026-09", metric_ids: ["arr"]});
  await call("cfo_unknown", "POST", "/api/metrics/sign", {period: "2026-09", metric_ids: ["nope"]});
  await call("cfo_refresh", "POST", "/api/metrics/refresh", {period: "2026-09"});
  await call("cfo_one", "GET", "/api/metrics/arr?period=2026-09");
  await call("cfo_one_unknown", "GET", "/api/metrics/nope?period=2026-09");
  await login("sales_staff");
  await call("sales_board_09_after", "GET", "/api/metrics/board?period=2026-09");
  await login("finance_staff");
  await call("fs_board_09_after", "GET", "/api/metrics/board?period=2026-09");

  await login("auditor");
  out.audit = (await D.request("GET", "/api/audit?after_seq=0&limit=1000")).entries;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
