// Demo-mode behaviour of the management report and the metric board (M3); owned by that milestone.
// It answers what the live API answers, from fixtures/report.js (recorded from the real graph and the
// real metric layer on the synthetic company), and refuses what the live API refuses:
// - POST /api/runs {app: report}: the persona's own recording for the asked period, in the language of
//   the input as live writes it (the page's default input is in the UI language, so the English page
//   gets the English recording), else in the UI language, else any (the shared rules in demo.js still
//   apply: start permission, thread level, user level, outage switches);
// - GET /api/metrics, /api/metrics/board, /api/metrics/<id>: rows filtered like row-level security does
//   (drafts and signed rows for their draft ACL, published rows for their audience, clearance >= level),
//   platform roles refused;
// - POST /api/metrics/sign, /publish, /refresh: head of finance only, every attempt audited;
// - POST /api/reports/<run>/export: own recorded runs only; the watermark is shown, nothing is downloaded.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});

  var PLATFORM = {platform_admin: 1, security_auditor: 1};
  var LEVEL_NAMES = {1: "L1 public", 2: "L2 internal", 3: "L3 confidential", 4: "L4 restricted"};
  var DEFAULT_PERIOD = "2026-Q3";
  var ZH_Q = {"一": "1", "二": "2", "三": "3", "四": "4"};

  // the same reading as corpai/agents/report/request.py, for the periods the recordings cover
  function periodOf(text) {
    var s = String(text || "");
    var m = s.match(/(\d{4})\s*-?\s*Q([1-4])/i);
    if (m) return m[1] + "-Q" + m[2];
    m = s.match(/Q([1-4])\s*[\/-]?\s*(\d{4})/i);
    if (m) return m[2] + "-Q" + m[1];
    m = s.match(/(\d{4})\s*年\s*(?:第\s*)?([1-4一二三四])\s*季度/);
    if (m) return m[1] + "-Q" + (ZH_Q[m[2]] || m[2]);
    m = s.match(/(\d{4})-(0[1-9]|1[0-2])(?!\d)/);
    if (m) return m[1] + "-" + m[2];
    m = s.match(/(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月/);
    if (m) return m[1] + "-" + (m[2].length === 1 ? "0" + m[2] : m[2]);
    return DEFAULT_PERIOD;
  }

  // the language request.py reads from the input: Chinese unless it has Latin words and no CJK
  function langOf(text) {
    var s = String(text || "");
    return /[㐀-鿿]/.test(s) || !/[A-Za-z]{2,}/.test(s) ? "zh" : "en";
  }

  function fx(ctx) { return ctx.appFixture.metrics || {defs: [], periods: {}, groups: {}}; }

  function reader(ctx, req) {
    if (!req.me) throw ctx.err(401, "unauthenticated", "Pick a persona first.");
    if (PLATFORM[req.me.role]) throw ctx.err(403, "forbidden", "Platform roles do not read business metrics.");
    return req.me;
  }

  function isCfo(me) { return !!me && me.role === "manager" && me.dept === "finance"; }

  function groupsOf(ctx, key) { return fx(ctx).groups[key] || []; }

  function statusOf(ctx, period, row) {
    return ctx.state.status[period + "|" + row.key] || row.status;
  }

  function intersects(a, b) { return (a || []).some(function (g) { return (b || []).indexOf(g) >= 0; }); }

  // row-level security as the database applies it: the ACL of the row's state, and clearance >= level
  function visibleRow(ctx, req, period, row) {
    var st = statusOf(ctx, period, row);
    var published = st === "published";
    var acl = published ? row.acl_published : row.acl_draft;
    var level = published ? 2 : row.level;
    if (!intersects(acl, groupsOf(ctx, req.persona)) || (req.me.clearance || 0) < level) return null;
    var out = ctx.copy(row);
    out.status = st;
    out.level = level;
    var prov = ctx.state.prov[period + "|" + row.key];
    if (prov) Object.keys(prov).forEach(function (k) { out.provenance[k] = prov[k]; });
    delete out.acl_draft;
    delete out.acl_published;
    return out;
  }

  function rowsOf(ctx, period, metricId) {
    return ((fx(ctx).periods[period] || {}).rows || []).filter(function (r) { return r.metric_id === metricId; });
  }

  function canOf(ctx, req) {
    var cfo = isCfo(req.me);
    var drafts = fx(ctx).defs.some(function (d) { return intersects(d.draft_acl, groupsOf(ctx, req.persona)); });
    return {sign: cfo, publish: cfo, refresh: cfo, drafts: drafts};
  }

  function listMetrics(ctx, req) {
    reader(ctx, req);
    var periods = {};
    var metrics = fx(ctx).defs.map(function (d) {
      var seen = [];
      Object.keys(fx(ctx).periods).sort().forEach(function (p) {
        var st = {};
        rowsOf(ctx, p, d.metric_id).forEach(function (r) {
          var v = visibleRow(ctx, req, p, r);
          if (v) st[v.status] = 1;
        });
        if (Object.keys(st).length) {
          seen.push({period: p, statuses: Object.keys(st).sort()});
          periods[p] = 1;
        }
      });
      var out = ctx.copy(d);
      out.periods = seen;
      out.access = seen.length ? "visible" : "no permission or not published";
      return out;
    });
    var can = canOf(ctx, req);
    // as live: the CFO's refresh offers ended periods, not only those that already have snapshots
    var refreshable = can.refresh ? Object.keys(fx(ctx).periods).sort().reverse() : [];
    return {metrics: metrics, periods: Object.keys(periods).sort().reverse(), can: can, refreshable: refreshable};
  }

  function needPeriod(ctx, req) {
    var p = req.query.period || "";
    if (!/^\d{4}-(0[1-9]|1[0-2]|Q[1-4])$/.test(p)) throw ctx.err(400, "bad_request", "period must look like 2026-09 or 2026-Q3.");
    return p;
  }

  function boardOf(ctx, req) {
    reader(ctx, req);
    var period = needPeriod(ctx, req);
    var list = listMetrics(ctx, req).metrics;
    return {period: period, can: canOf(ctx, req), metrics: list.map(function (m) {
      var rows = rowsOf(ctx, period, m.metric_id).map(function (r) { return visibleRow(ctx, req, period, r); })
        .filter(Boolean);
      var st = (m.periods.filter(function (x) { return x.period === period; })[0] || {}).statuses || [];
      return {metric_id: m.metric_id, name_zh: m.name_zh, name_en: m.name_en, unit: m.unit, level: m.level,
              visible_to: m.visible_to, statuses: st, rows: rows};
    })};
  }

  function oneMetric(ctx, req) {
    reader(ctx, req);
    var id = req.match[1];
    var def = fx(ctx).defs.filter(function (d) { return d.metric_id === id; })[0];
    if (!def) throw ctx.err(404, "not_found", "No such metric.");
    var period = needPeriod(ctx, req);
    var key = id + (req.query.dims ? "@" + req.query.dims.split(",").sort().join("&") : "");
    var row = rowsOf(ctx, period, id).filter(function (r) { return r.key === key; })[0];
    var v = row ? visibleRow(ctx, req, period, row) : null;
    if (v) return v;
    return {metric_id: id, period: period, dims: {}, value: null, unit: def.unit, status: "unavailable", level: 1,
            suppressed: false, provenance: {}, reason: "no permission or not published", key: key,
            name_zh: def.name_zh, name_en: def.name_en, display_zh: "[无权限或尚未发布]",
            display_en: "[no permission or not published]"};
  }

  // sign / publish: head of finance only (the live API checks in Python and again in SQL)
  function transition(ctx, req, from, to, event, action) {
    var me = reader(ctx, req);
    var period = String((req.body || {}).period || "");
    var ids = (req.body || {}).metric_ids;
    if (!/^\d{4}-(0[1-9]|1[0-2]|Q[1-4])$/.test(period)) throw ctx.err(400, "bad_request", "period must look like 2026-09 or 2026-Q3.");
    if (!Array.isArray(ids) || !ids.length) throw ctx.err(400, "bad_request", "Name at least one metric.");
    var known = fx(ctx).defs.map(function (d) { return d.metric_id; });
    if (ids.some(function (i) { return known.indexOf(i) < 0; })) throw ctx.err(400, "bad_request", "Unknown metric id; pick metrics from the list.");
    if (!isCfo(me)) {
      ctx.audit({actor: me.user_id, event: event, action: action, resource: "fin.metric:" + period, decision: "deny",
                 reason: "only the head of finance may sign off or publish metrics", details: {metrics: ids}});
      throw ctx.err(403, "forbidden", "Only the head of finance signs off, publishes or refreshes metrics.");
    }
    var changed = {};
    var n = 0;
    ids.forEach(function (id) {
      rowsOf(ctx, period, id).forEach(function (r) {
        if (statusOf(ctx, period, r) !== from) return;
        ctx.state.status[period + "|" + r.key] = to;
        var prov = ctx.state.prov[period + "|" + r.key] || (ctx.state.prov[period + "|" + r.key] = {});
        if (to === "signed") { prov.signed_by = me.user_id; prov.signed_at = new Date().toISOString(); }
        if (to === "published") prov.published_at = new Date().toISOString();
        changed[id] = 1;
        n += 1;
      });
    });
    var ch = Object.keys(changed).sort();
    var res = {period: period, changed: ch, rows: n, skipped: ids.filter(function (i) { return !changed[i]; })};
    ctx.audit({actor: me.user_id, event: event, action: action, resource: "fin.metric:" + period, decision: "allow",
               reason: action + " by the head of finance", details: {metrics: ch, rows: n, skipped: res.skipped}});
    return res;
  }

  function refresh(ctx, req) {
    var me = reader(ctx, req);
    var period = String((req.body || {}).period || "");
    if (!/^\d{4}-(0[1-9]|1[0-2]|Q[1-4])$/.test(period)) throw ctx.err(400, "bad_request", "period must look like 2026-09 or 2026-Q3.");
    if (!isCfo(me)) {
      ctx.audit({actor: me.user_id, event: "policy_decision", action: "metrics.refresh", resource: "fin.metric:" + period,
                 decision: "deny", reason: "only the head of finance, and only in the demo"});
      throw ctx.err(403, "forbidden", "Only the head of finance signs off, publishes or refreshes metrics.");
    }
    var rows = ((fx(ctx).periods[period] || {}).rows || []);
    ctx.audit({actor: me.user_id, event: "policy_decision", action: "metrics.refresh", resource: "fin.metric:" + period,
               decision: "allow", reason: "demo refresh by the head of finance"});
    ctx.audit({actor: "svc:metrics", event: "owner_job", action: null, decision: "allow", details: {period: period}});
    // the recorded data does not change in the demo: every row comes back unchanged
    return {period: period, metrics: fx(ctx).defs.length, rows: rows.length, inserted: 0, updated: 0,
            unchanged: rows.length, suppressed: rows.filter(function (r) { return r.suppressed; }).length, new_versions: 0};
  }

  function slug(s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "report"; }

  // POST /api/reports/<run>/export: the persona's own recorded report only; same watermark as live
  function exportRun(ctx, req) {
    if (!req.me) throw ctx.err(401, "unauthenticated", "Pick a persona first.");
    var runId = req.match[1];
    var item = (ctx.appFixture.runs || []).filter(function (it) {
      return it.run && it.run.run_id === runId && it.persona_key === req.persona;
    })[0];
    if (!item || !(req.body && req.body.thread_id === item.run.thread_id)) {
      throw ctx.err(404, "not_found", "No such report among yours; run the report first.");
    }
    var data = item.run.output.data;
    var level = Math.max(item.run.hwm || 4, data.level || 4);
    if (!data.export || !data.export.allowed) {
      ctx.audit({actor: req.me.user_id, event: "export", action: "export_report", resource: "report:" + runId,
                 decision: "deny", reason: "policy", run_id: runId, level: level});
      throw ctx.err(403, "forbidden", "Your role may not export this report.");
    }
    var now = new Date();
    var ts = now.toISOString().slice(0, 19) + "Z";
    var mark = "WATERMARK | exported by " + req.me.user_id + " " + req.me.name + " | " + ts + " UTC | run " + runId +
      " | " + LEVEL_NAMES[level] + " | do not forward";
    var title = String(data.title || "Management report");
    var body = String(item.run.output.text || "");
    // the export writes the title itself, between the watermark and the body (as live does)
    if (body.indexOf("# " + title + "\n") === 0) body = body.slice(title.length + 3).replace(/^\n+/, "");
    var content = "> " + mark + "\n\n# " + title + "\n\n" + body.replace(/\n+$/, "") +
      "\n\n---\n\n> " + mark + "\n<!-- corpai-export v1 demo: the live server adds a sha256 here -->\n";
    var filename = slug(title) + "-" + runId + "-" + ts.replace(/[-:]/g, "") + ".md";
    ctx.audit({actor: req.me.user_id, event: "export", action: "export_report", resource: "report:" + runId,
               decision: "allow", reason: "watermarked export", run_id: runId, level: level,
               details: {filename: filename, domain: data.export.domain}});
    return {filename: filename, content: content, sha256: "", level: level, domain: data.export.domain,
            bytes: content.length};
  }

  KB.demo.register("report", {
    apps: ["report"],
    // the persona's own recording for the period asked for, in the input's language if recorded,
    // else in the UI language, else any; null falls back to the shared lookup
    findRun: function (ctx, personaKey, body) {
      var period = periodOf(body.input);
      var mine = (ctx.appFixture.runs || []).filter(function (it) {
        return it.persona_key === personaKey && it.app === "report" && it.run && it.run.output &&
          it.run.output.data && it.run.output.data.period === period;
      });
      function inLang(lang) { return mine.filter(function (it) { return it.run.output.data.lang === lang; })[0]; }
      var ui = KB.lang && KB.lang.current;
      return inLang(langOf(body.input)) || (ui ? inLang(ui) : null) || mine[0] || null;
    },
    routes: [
      {method: "GET", path: /^\/api\/metrics$/, handle: listMetrics},
      {method: "GET", path: /^\/api\/metrics\/board$/, handle: boardOf},
      {method: "GET", path: /^\/api\/metrics\/([a-z][a-z0-9_]*)$/, handle: oneMetric},
      {method: "POST", path: /^\/api\/metrics\/sign$/, handle: function (ctx, req) {
        return transition(ctx, req, "draft", "signed", "metric_signed", "metrics.sign");
      }},
      {method: "POST", path: /^\/api\/metrics\/publish$/, handle: function (ctx, req) {
        return transition(ctx, req, "signed", "published", "metric_published", "metrics.publish");
      }},
      {method: "POST", path: /^\/api\/metrics\/refresh$/, handle: refresh},
      {method: "POST", path: /^\/api\/reports\/(run_[0-9a-z]{20})\/export$/, handle: exportRun}
    ],
    init: function (ctx) {
      ctx.state.status = {};   // "<period>|<key>" -> status after a demo sign-off or publication
      ctx.state.prov = {};     // "<period>|<key>" -> signed_by / signed_at / published_at set in the demo
    },
    periodOf: periodOf,
    langOf: langOf
  });
})();
