// Demo-mode behaviour of Account profile (M4); owned by that milestone.
// - Recordings live in fixtures/sales.js; this lookup prefers them (the shared runs.js still holds an
//   older sample of this app). Every run it finds still goes through the shared rules in demo.js:
//   start permission, the thread's level, the user's level and the outage switches.
// - "/followup <contact>" is answered only in a conversation that holds that company's profile,
//   like the live graph; anywhere else it is refused the same way (nothing is drafted).
// - GET /api/sales/accounts answers from the recording of the persona's own CRM view.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var FOLLOWUP = /^\/followup(\s|$)/i;

  function norm(s) {
    return String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
  }

  function own(ctx, key, app, input) {
    var runs = ctx.appFixture.runs || [];
    var q = norm(input);
    var mine = runs.filter(function (it) { return it.app === app && norm(it.input) === q && it.persona_key === key; });
    var any = runs.filter(function (it) { return it.app === app && norm(it.input) === q && it.persona_key === "*"; });
    return mine[0] || any[0] || null;
  }

  // What the live graph answers to a follow-up without a profile in the conversation.
  function refusedFollowup(ctx, body) {
    return {persona_key: "*", app: body.app, input: body.input, run: {
      run_id: "run_demo_followup_refused", thread_id: body.thread_id || "thr_demo_followup", app: body.app,
      status: "done", hwm: 1, route: null, pending_approval: null, error: null,
      output: {text: "Profile a company in this conversation first, then draft the follow-up.", citations: [],
               data: {kind: "sales_followup", refused: "no_profile"}},
      events: [{t: "notice", code: "followup_needs_profile", zh: "请先在这个会话里为一家公司生成画像，再起草跟进邮件。",
                en: "Profile a company in this conversation first, then draft the follow-up."}]
    }};
  }

  function findRun(ctx, key, body) {
    var profiles = ctx.state.profiles || (ctx.state.profiles = {});
    if (FOLLOWUP.test(String(body.input || "").trim())) {
      var item = own(ctx, key, body.app, body.input);
      var held = body.thread_id ? profiles[body.thread_id] : null;
      if (!held) return refusedFollowup(ctx, body);
      // only a recording drafted after exactly this profile; refused here so that the shared lookup
      // cannot hand out the same input recorded for another company
      if (!(item && norm(item.after) === held)) {
        throw ctx.err(404, "demo_not_recorded", "This step has no recording in demo mode.");
      }
      return item;
    }
    var hit = own(ctx, key, body.app, body.input);
    var data = hit && hit.run.output && hit.run.output.data;
    if (data && data.followup && data.followup.available) {
      profiles[body.thread_id || hit.run.thread_id] = norm(hit.input);
    }
    return hit;
  }

  // The same answer as findRun, without remembering anything (the page marks unrecorded controls).
  function hasRecording(ctx, key, body) {
    var item = own(ctx, key, body.app, body.input);
    if (!FOLLOWUP.test(String(body.input || "").trim())) return !!item;
    var held = body.thread_id ? (ctx.state.profiles || {})[body.thread_id] : null;
    return !!(held && item && norm(item.after) === held);
  }

  function recordedPersonas(ctx, app) {
    var keys = [];
    (ctx.appFixture.runs || []).forEach(function (it) {
      if (it.app === app && !it.switches && keys.indexOf(it.persona_key) < 0) keys.push(it.persona_key);
    });
    return keys.indexOf("*") >= 0 ? null : keys;
  }

  KB.demo.register("sales", {
    apps: ["sales_profile"],
    findRun: findRun,
    hasRecording: hasRecording,
    recordedPersonas: recordedPersonas,
    init: function (ctx) { ctx.state.profiles = {}; },
    routes: [{
      method: "GET", path: /^\/api\/sales\/accounts$/,
      handle: function (ctx, req) {
        var me = req.me || {};
        if (!(me.apps || []).some(function (a) { return a.id === "sales_profile"; })) {
          throw ctx.err(403, "forbidden", "Your role may not start sales_profile.");
        }
        var by = (ctx.appFixture.accounts && ctx.appFixture.accounts.by_persona) || {};
        var q = norm(req.query.q);
        var rows = (by[req.persona] || []).filter(function (a) { return !q || norm(a.name).indexOf(q) >= 0; });
        return {accounts: ctx.copy(rows), denied: [], error: null};
      }
    }]
  });
})();
