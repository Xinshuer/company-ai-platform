// Demo-mode behaviour of Contract review (M2); owned by that milestone.
// Recordings live in fixtures/legal.js: "runs" (merged by js/demo.js like every milestone's),
// "no_model_runs" (due-contracts and send runs, which make no model call and so carry route null,
// as the live API reports them), "traces", "proposals" (the pending send and draft proposals in
// legal_staff's and legal_head's boxes), and per persona the "contracts" and "reviews" lists.
//
// The page sends a JSON request per mode (corpai/agents/legal/_input.py); a recording is found by
// the request's meaning, not its spelling (key order, an omitted month = next month, plain-text
// "review C-0004"), and only for the persona that recorded it. Every run then goes through the
// shared rules in js/demo.js (start permission, thread level, user level, outage switches). The
// /api/legal/* routes refuse personas without the app (403) like the live API, and a review some
// persona may not read answers 404.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});

  var APP = "legal_review";
  var DUE_WORDS = /到期|续约|通知期|expir|due\b|renewal|notice period|ablauf|kündigung/i;

  function nextMonth(simToday) {
    var m = /^(\d{4})-(\d{2})/.exec(simToday || "2026-10-01");
    var y = parseInt(m[1], 10);
    var mo = parseInt(m[2], 10) + 1;
    if (mo > 12) { mo = 1; y += 1; }
    return y + "-" + (mo < 10 ? "0" : "") + mo;
  }

  // canonical JSON: sorted keys, so {"a":1,"b":2} and {"b":2,"a":1} compare equal
  function canon(v) {
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    if (v && typeof v === "object") {
      return "{" + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ":" + canon(v[k]); }).join(",") + "}";
    }
    return JSON.stringify(v === undefined ? null : v);
  }

  // The meaning of a request, mirroring the server's parse (corpai/agents/legal/_input.py).
  function meaning(input, simToday) {
    var s = String(input || "").trim();
    var r = null;
    if (s.charAt(0) === "{") {
      try { r = JSON.parse(s); } catch (e) { return null; }
    } else if (s.length < 200) {
      var ids = s.match(/\bC-[0-9]{4}\b/g) || [];
      if (DUE_WORDS.test(s)) {
        var mm = /\b(20[0-9]{2}-(?:0[1-9]|1[0-2]))\b/.exec(s);
        r = {mode: "due", month: mm ? mm[1] : null};
      } else if (ids.length === 1 || (ids.length > 1 && ids.every(function (x) { return x === ids[0]; }))) {
        r = {mode: "review", contract_id: ids[0]};
      }
    }
    if (!r || typeof r !== "object") return null;
    if (r.mode === "due") return {mode: "due", month: r.month || nextMonth(simToday)};
    if (r.mode === "review") {
      return {mode: "review", contract_id: r.contract_id || null, text: r.text || "", upload_id: r.upload_id || null};
    }
    if (r.mode === "send") {
      return {mode: "send", contract_id: r.contract_id || null, to: String(r.to || "").trim(),
              note: String(r.note || "").trim()};
    }
    if (r.mode === "draft") {
      return {mode: "draft", template_type: r.template_type || null, account_id: r.account_id || null,
              variables: r.variables || {}, instructions: String(r.instructions || "").trim()};
    }
    return null;
  }

  function items(ctx) {
    var fx = ctx.appFixture || {};
    return (fx.runs || []).concat(fx.no_model_runs || []);
  }

  function simToday(ctx) {
    return ((ctx.fixtures.health || {}).sim_today) || "2026-10-01";
  }

  function hasApp(me) {
    return !!me && (me.apps || []).some(function (a) { return a.id === APP; });
  }

  // The /api/legal/* routes are for people who may start the app (403 otherwise, as live).
  function need(ctx, req) {
    if (!hasApp(req.me)) throw ctx.err(403, "forbidden", "Only the legal department and the CEO use the legal pages.");
    return ctx.appFixture || {};
  }

  function reviewsOf(ctx, req) {
    var fx = need(ctx, req);
    return ctx.copy(((fx.reviews || {})[req.persona]) || []);
  }

  function reviewById(ctx, id) {
    var all = items(ctx);
    for (var i = 0; i < all.length; i++) {
      var d = all[i].run && all[i].run.output && all[i].run.output.data;
      if (d && d.review && d.review.review_id === id) return ctx.copy(d.review);
    }
    return null;
  }

  function recorded(ctx, personaKey, body) {
    var want = meaning(body.input, simToday(ctx));
    if (!want) return null;
    var key = canon(want);
    var found = items(ctx).filter(function (it) {
      return it.app === APP && (it.persona_key === personaKey || it.persona_key === "*") &&
        canon(meaning(it.input, simToday(ctx))) === key;
    });
    found.sort(function (a, b) { return (b.persona_key === personaKey ? 1 : 0) - (a.persona_key === personaKey ? 1 : 0); });
    return found[0] || null;
  }

  KB.demo.register("legal", {
    apps: [APP],
    findRun: recorded,
    // pure lookups for the page: mark requests with no recording, say when a persona has none
    hasRecording: function (ctx, personaKey, body) { return !!recorded(ctx, personaKey, body); },
    recordedPersonas: function (ctx) {
      var keys = [];
      items(ctx).forEach(function (it) {
        if (it.app === APP && !it.switches && keys.indexOf(it.persona_key) < 0) keys.push(it.persona_key);
      });
      return keys.indexOf("*") >= 0 ? null : keys;
    },
    routes: [
      {method: "GET", path: /^\/api\/legal\/contracts$/, handle: function (ctx, req) {
        var fx = need(ctx, req);
        var list = ctx.copy(((fx.contracts || {})[req.persona]) || []);
        if (req.query.status) list = list.filter(function (c) { return c.status === req.query.status; });
        return {contracts: list, count: list.length};
      }},
      {method: "GET", path: /^\/api\/legal\/reviews$/, handle: function (ctx, req) {
        var list = reviewsOf(ctx, req);
        if (req.query.contract_id) list = list.filter(function (r) { return r.contract_id === req.query.contract_id; });
        return {reviews: list, count: list.length};
      }},
      {method: "GET", path: /^\/api\/legal\/reviews\/([^/]+)$/, handle: function (ctx, req) {
        var id = req.match[1];
        // only reviews this persona may read (the recorded list is what row-level security returned)
        var visible = reviewsOf(ctx, req).some(function (r) { return r.review_id === id; });
        var review = visible ? reviewById(ctx, id) : null;
        if (!review) throw ctx.err(404, "not_found", "No such review among those you may read.");
        review.stored = true;
        return review;
      }},
      {method: "POST", path: /^\/api\/legal\/uploads$/, handle: function (ctx, req) {
        need(ctx, req);
        var text = String((req.body && req.body.text) || "");
        if (!text.trim()) throw ctx.err(400, "bad_request", "the file is empty");
        if (text.length > 200000) throw ctx.err(400, "bad_request", "the contract is too long (max 200000 characters)");
        ctx.state.uploads = (ctx.state.uploads || 0) + 1;
        // a demo upload has no recording: reviewing it answers demo_not_recorded, as the page says
        return {upload_id: "upl_demo" + String(ctx.state.uploads), chars: text.length, sha256: null,
                filename: String((req.body && req.body.filename) || "").slice(0, 120), expires_in_s: 7200};
      }},
      // Demo only (the live API has no such route): the recorded requests, to pre-fill the forms.
      {method: "GET", path: /^\/api\/legal\/samples$/, handle: function (ctx, req) {
        need(ctx, req);
        var out = [];
        items(ctx).forEach(function (it) {
          if (it.persona_key !== req.persona) return;
          var m = meaning(it.input, simToday(ctx));
          if (m) out.push(m);
        });
        return {requests: out};
      }}
    ]
  });
})();
