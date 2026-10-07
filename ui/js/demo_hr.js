// Demo-mode behaviour of the HR apps (M4); owned by that milestone. Mirrors the live API:
// - hr_screening and hr_headcount runs come from fixtures/hr.js (the shared runs.js still holds older
//   samples) and still go through the shared rules in demo.js (start permission, levels, switches);
// - POST /api/hr/screenings/decision: only on the persona's own screening conversation that waits for
//   exactly this candidate, with a decision of advance / hold / reject and a non-empty reason; every
//   attempt is audited as hr_decision, refusals included; the resumed run is built here, without
//   the reason (the live graph never holds it either);
// - GET /api/hr/candidates and GET /api/hr/candidates/<id>: the persona's own recorded view, only for
//   an app the persona may start; anything else is an explicit denial or "not found".
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var CAND = /\bCAND-[A-Za-z0-9]{3,24}\b/;
  var DECISIONS = ["advance", "reject", "hold"];
  var READ_APPS = ["hr_candidates", "hr_screening"];
  var NOT_HM = "you are not the hiring manager or recruiter of any requisition";

  function hasApp(me, app) {
    return !!me && (me.apps || []).some(function (a) { return a.id === app; });
  }

  function findRun(ctx, key, body) {
    var runs = (ctx.appFixture.runs || []).filter(function (it) {
      return it.app === body.app && (it.persona_key === key || it.persona_key === "*");
    });
    if (body.app === "hr_headcount") {
      return runs.filter(function (it) { return it.persona_key === key; })[0] || runs[0] || null;
    }
    if (body.app !== "hr_screening") return null;
    var m = CAND.exec(String(body.input || ""));
    if (!m) return null;
    var hit = runs.filter(function (it) { return it.persona_key === key && it.input === m[0]; })[0] || null;
    // the recording waits for a decision (unless local_down will turn it into an error)
    if (hit && !KB.demo.switches().local_down) {
      ctx.state.waiting[body.thread_id || hit.run.thread_id] = {persona: key, candidate_id: m[0], item: hit};
    }
    return hit;
  }

  // The same answer as findRun without remembering a waiting conversation (pure, for the page).
  function hasRecording(ctx, key, body) {
    var runs = (ctx.appFixture.runs || []).filter(function (it) {
      return it.app === body.app && !it.switches && (it.persona_key === key || it.persona_key === "*");
    });
    if (body.app === "hr_headcount") return runs.length > 0;
    if (body.app !== "hr_screening") return false;
    var m = CAND.exec(String(body.input || ""));
    return !!m && runs.some(function (it) { return it.persona_key === key && it.input === m[0]; });
  }

  function recordedPersonas(ctx, app) {
    if (app === "hr_candidates") return null;  // code only: answered from each persona's recorded view
    var keys = [];
    (ctx.appFixture.runs || []).forEach(function (it) {
      if (it.app === app && !it.switches && keys.indexOf(it.persona_key) < 0) keys.push(it.persona_key);
    });
    return keys.indexOf("*") >= 0 ? null : keys;
  }

  // as on the server: the caller must be allowed to start the app whose agent reads the data
  function need(ctx, req, app) {
    if (!hasApp(req.me, app)) throw ctx.err(403, "forbidden", "your role may not start " + app + " (agent-start-denied)");
  }

  function candidates(ctx, req) {
    var app = req.query.app || "hr_candidates";
    if (READ_APPS.indexOf(app) < 0) throw ctx.err(400, "bad_request", "app must be hr_candidates or hr_screening.");
    need(ctx, req, app);
    var rec = ((ctx.appFixture.candidates || {})[app] || {})[req.persona];
    var data = rec ? ctx.copy(rec) : {requisitions: [], denied: [{id: "hr.requisition:*", reason: NOT_HM}], own: [], listed: []};
    var reqId = req.query.req_id;
    if (reqId) {
      if (!/^R-[0-9]{2}$/.test(reqId)) throw ctx.err(400, "bad_request", "req_id looks like R-01.");
      var one = data.requisitions.filter(function (r) { return r.req_id === reqId; });
      data.requisitions = one.length ? one : [{req_id: reqId, title: null, status: null, own: false, candidates: [], denied: true}];
      data.denied = one.length ? [] : [{id: "hr.requisition:" + reqId, reason: "no permission"}];
    }
    data.app = app;
    return data;
  }

  function resume(ctx, req) {
    var app = req.query.app || "hr_candidates";
    if (READ_APPS.indexOf(app) < 0) throw ctx.err(400, "bad_request", "app must be hr_candidates or hr_screening.");
    need(ctx, req, app);
    var r = ctx.appFixture.resumes || {};
    var access = ((r.access || {})[app] || {})[req.persona] || [];
    var id = req.match[1];
    // hidden and missing look the same, as with row-level security on the server
    if (access.indexOf(id) < 0 || !(r.items || {})[id]) {
      throw ctx.err(404, "not_found", "No such candidate, or you have no permission to see them.");
    }
    return ctx.copy(r.items[id]);
  }

  function decide(ctx, req) {
    need(ctx, req, "hr_screening");
    var b = req.body || {};
    var w = ctx.state.waiting[b.thread_id];
    var audit = function (allow, why, decision, screeningId) {
      ctx.audit({actor: req.me.user_id, act_chain: ["app:hr_screening"], event: "hr_decision",
                 action: "hr.record_decision", resource: "hr.candidate:" + String(b.candidate_id || ""),
                 decision: allow ? "allow" : "deny", reason: why, level: 4,
                 details: {decision: DECISIONS.indexOf(decision) >= 0 ? decision : "invalid", screening_id: screeningId || null}});
    };
    if (!w || w.persona !== req.persona) throw ctx.err(404, "not_found", "No such conversation among yours.");
    if (w.candidate_id !== b.candidate_id) {
      throw ctx.err(400, "bad_request", "This conversation is not waiting for a decision on that candidate; screen the candidate first.");
    }
    if (DECISIONS.indexOf(b.decision) < 0) {
      audit(false, "invalid_decision", b.decision);
      throw ctx.err(400, "bad_request", "decision must be advance, reject or hold");
    }
    if (typeof b.reason !== "string" || !b.reason.trim() || b.reason.length > 2000) {
      audit(false, "reason_required", b.decision);
      throw ctx.err(400, "bad_request", "a decision needs a reason: say why, in your own words (up to 2000 characters)");
    }
    ctx.state.n += 1;
    var sid = "scr_demo" + String(ctx.state.n);
    audit(true, "human_decision", b.decision, sid);
    delete ctx.state.waiting[b.thread_id];
    var run = ctx.copy(w.item.run);
    run.run_id = "run_demo_decision" + String(ctx.state.n);
    run.thread_id = b.thread_id;
    run.status = "done";
    run.route = null;  // recording the decision calls no model
    run.events = [];
    run.pending_approval = null;
    run.error = null;
    run.output.text = "Decision recorded for " + b.candidate_id + ": " + b.decision + " (" + sid + "). The reason is stored with it.";
    run.output.data.awaiting_decision = false;
    run.output.data.decision = {decision: b.decision, screening_id: sid, decided_by: req.me.user_id};
    return {screening_id: sid, decision: b.decision, candidate_id: b.candidate_id, run: run};
  }

  KB.demo.register("hr", {
    apps: ["hr_screening", "hr_headcount", "hr_candidates"],
    findRun: findRun,
    hasRecording: hasRecording,
    recordedPersonas: recordedPersonas,
    init: function (ctx) { ctx.state.waiting = {}; ctx.state.n = 0; },
    routes: [
      {method: "GET", path: /^\/api\/hr\/candidates$/, handle: candidates},
      {method: "GET", path: /^\/api\/hr\/candidates\/([^/]+)$/, handle: resume},
      {method: "POST", path: /^\/api\/hr\/screenings\/decision$/, handle: decide}
    ]
  });
})();
