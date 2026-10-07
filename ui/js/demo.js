// Demo backend: answers the §18 API from window.FIXTURES so the page works from file://.
// It mirrors the server's security behaviour where the UI depends on it (403 for apps the
// persona may not start, own traces only, four-eyes on tier 3, params hash re-check, auditor
// and admin only pages, a thread's level that only goes up, local_down refusing every local
// call, approvers not seeing the requester's resumed thread), so demo mode never shows
// something live mode would refuse.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var F = {};
  var S = {};

  // (Re)read window.FIXTURES: js/fixtures_loader.js adds the files only once the mode is demo.
  function reload() {
    F = window.FIXTURES || {};
    S = {
      persona: S.persona || null,   // persona_key of the "logged in" demo user
      switches: U.copy(F.switches || {local_down: false, external_down: false}),
      proposals: U.copy(F.proposals || {by_persona: {}, resumed_runs: {}}),
      audit: U.copy(F.audit || {entries: [], chain: {ok: true, checked: 0, first_bad_seq: null}}),
      threads: {},                  // thread_id -> highest level reached in that thread
      tampered: false
    };
  }
  reload();

  function err(status, code, message) {
    var e = new Error(message || code);
    e.status = status;
    e.code = code;
    e.serverMessage = message || "";
    return e;
  }

  function personaList() {
    return (F.personas && F.personas.personas) || [];
  }

  function meFor(key) {
    var by = (F.me && F.me.by_persona) || {};
    return by[key] ? U.copy(by[key]) : null;
  }

  function norm(s) {
    return String(s || "").toLowerCase().replace(/[\s？?。.！!，,]+/g, " ").trim();
  }

  function matches(item, key, app) {
    return item.app === app && (item.persona_key === key || item.persona_key === "*");
  }

  function switchesMatch(item) {
    var want = item.switches;
    if (!want) return true;
    return Object.keys(want).every(function (k) { return !!S.switches[k] === !!want[k]; });
  }

  function auditAppend(e) {
    var entries = S.audit.entries;
    var seq = entries.length ? entries[entries.length - 1].seq + 1 : 1;
    var row = {
      seq: seq, ts: new Date().toISOString(), actor: e.actor, act_chain: e.act_chain || [],
      event: e.event, action: e.action || null, resource: e.resource || null,
      decision: e.decision || null, reason: e.reason || null, route: e.route || null,
      level: e.level || null, approval_id: e.approval_id || null, run_id: e.run_id || null,
      details: e.details || {}
    };
    entries.push(row);
    S.audit.chain.checked = seq;
  }

  // A thread keeps its level (architecture §12: nothing lowers it; a new thread is the only reset).
  // A recording made in a fresh thread is raised to the level the thread already reached, with
  // the route that level implies; the local-mode notice belongs to the run that crossed L3.
  function applyThreadLevel(run, threadLevel) {
    var th = parseInt(threadLevel, 10) || 0;
    if (!th) return run;
    var out = U.copy(run);
    // what the thread already holds raised nothing in this run: drop those hwm steps, start the rest at th
    var events = (out.events || []).filter(function (e) { return !(e.t === "hwm" && e.to <= th); })
      .map(function (e) { return e.t === "hwm" && e.from < th ? U.copy(Object.assign({}, e, {from: th})) : e; });
    if (th >= 3) events = events.filter(function (e) { return e.t !== "notice" || e.code !== "local_mode"; });
    if (th > (out.hwm || 1)) {
      events = events.filter(function (e) { return e.t !== "route"; });
      out.hwm = th;
      var route = th >= 3 ? "local" : out.route;
      events.push({t: "route", route: route, level: th, reason: "route_by_level"});
      out.route = route;
    }
    out.events = events;
    return out;
  }

  // Apply the gateway's switch rules (architecture §8 rule 4) to a recorded run:
  // external_down sends L1/L2 to local (the safe direction); local_down refuses local calls.
  // Nothing is ever moved from local to external.
  function applySwitches(run) {
    var out = U.copy(run);
    if (out.status === "error") return out;
    if (out.route === "external" && S.switches.external_down) {
      out.route = "local";
      out.events = (out.events || []).concat([
        {t: "route", route: "local", level: out.hwm, reason: "external_down_fallback"}
      ]);
    }
    if (out.route === "local" && S.switches.local_down) {
      var kept = (out.events || []).filter(function (e) { return e.t !== "notice" && e.t !== "route"; });
      kept.push({t: "route", route: "local", level: out.hwm, reason: "local_down"});
      out.status = "error";
      out.events = kept;
      out.output = {text: "", citations: [], data: {}};
      out.pending_approval = null;
      out.error = {
        code: "local_unavailable",
        message: "The local model is unavailable. Confidential requests never fall back to the external route; try again later or ask the platform admin."
      };
    }
    return out;
  }

  // The user may raise the level of a run, never lower it (architecture §12 user_level).
  function applyUserLevel(run, userLevel) {
    var lvl = parseInt(userLevel, 10) || 1;
    if (lvl <= (run.hwm || 1)) return run;
    var out = U.copy(run);
    out.events = (out.events || []).filter(function (e) { return e.t !== "route"; });
    out.events.push({t: "hwm", from: run.hwm || 1, to: lvl, reason: "user_level"});
    out.hwm = lvl;
    var route = lvl >= 3 ? "local" : out.route;
    out.events.push({t: "route", route: route, level: lvl, reason: "route_by_level"});
    if (route === "local" && out.route !== "local") {
      out.events.push({t: "notice", code: "local_mode", zh: "本会话已切换到本地模式", en: "This session now uses the local model"});
    }
    out.route = route;
    return out;
  }

  function findRun(key, body) {
    var items = (F.runs && F.runs.items) || [];
    var q = norm(body.input);
    var cands = items.filter(function (it) { return matches(it, key, body.app) && norm(it.input) === q; });
    // most specific first: own persona over "*", and a recording made under the current switches
    cands.sort(function (a, b) {
      var sa = (a.persona_key === key ? 2 : 0) + (a.switches ? 1 : 0);
      var sb = (b.persona_key === key ? 2 : 0) + (b.switches ? 1 : 0);
      return sb - sa;
    });
    for (var i = 0; i < cands.length; i++) {
      if (switchesMatch(cands[i])) return cands[i];
    }
    return null;
  }

  function runAs(key, body) {
    var me = meFor(key);
    if (!me) throw err(401, "unauthenticated", "Pick a persona first.");
    var allowed = (me.apps || []).some(function (a) { return a.id === body.app; });
    if (!allowed) throw err(403, "forbidden", "Your role may not start this app.");
    if (!body.input || !String(body.input).trim()) throw err(400, "bad_request", "Type a question first.");
    var item = findRun(key, body);
    if (!item) {
      throw err(404, "demo_not_recorded", "This question has no recording in demo mode.");
    }
    // Order as on the server: the level first (thread, then the user's choice), then the
    // gateway's switches decide on the final route, so local_down refuses an L3/L4 request
    // even when the recording itself went external.
    var thread = body.thread_id ? (S.threads[body.thread_id] || 0) : 0;
    var run = applyUserLevel(applyThreadLevel(U.copy(item.run), thread), body.user_level);
    if (!item.switches) run = applySwitches(run);
    if (body.thread_id) run.thread_id = body.thread_id;
    if (run.thread_id) S.threads[run.thread_id] = Math.max(S.threads[run.thread_id] || 0, run.hwm || 1);
    auditAppend({actor: me.user_id, act_chain: ["agent:" + body.app], event: "model_call",
                 decision: run.status === "error" ? "deny" : "allow",
                 reason: run.status === "error" ? "local_down" : "route_by_level",
                 route: run.route, level: run.hwm, run_id: run.run_id});
    return run;
  }

  function runOwner(runId) {
    var items = (F.runs && F.runs.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].run && items[i].run.run_id === runId) return items[i].persona_key;
    }
    return null;
  }

  function boxes(key) {
    var by = S.proposals.by_persona || {};
    if (!by[key]) by[key] = {mine: [], to_approve: []};
    return by[key];
  }

  function findProposal(key, id) {
    var b = boxes(key);
    var all = (b.to_approve || []).concat(b.mine || []);
    for (var i = 0; i < all.length; i++) if (all[i].proposal_id === id) return all[i];
    return null;
  }

  // A decision is visible to everybody who holds the proposal in a box.
  function updateEverywhere(id, patch) {
    var by = S.proposals.by_persona || {};
    Object.keys(by).forEach(function (k) {
      ["mine", "to_approve"].forEach(function (box) {
        (by[k][box] || []).forEach(function (p) {
          if (p.proposal_id === id) {
            Object.keys(patch).forEach(function (f) { p[f] = U.copy(patch[f]); });
          }
        });
      });
    });
  }

  function decide(key, id, body, approve) {
    var me = meFor(key);
    if (!me) throw err(401, "unauthenticated", "Pick a persona first.");
    var p = findProposal(key, id);
    if (!p) throw err(404, "not_found", "No such proposal in your inbox.");
    if (p.status !== "pending") throw err(400, "bad_request", "This proposal was already decided.");
    if (!p.can_approve) throw err(403, "forbidden", p.why_not || "You may not decide this proposal.");
    var reason = String((body && body.reason) || "").trim();
    if (!reason) throw err(400, "bad_request", "Give a reason for the decision.");
    if (approve && body.params_hash !== p.params_hash) {
      throw err(400, "bad_request", "The parameters changed since you looked at them; reload and check again.");
    }
    var now = new Date().toISOString();
    var resumedRec = approve ? (S.proposals.resumed_runs || {})[id] : null;
    var recOutbox = resumedRec && resumedRec.output && resumedRec.output.data && resumedRec.output.data.outbox_id;
    var patch = {
      status: approve ? "executed" : "rejected",
      approver: {user_id: me.user_id, name: me.name},
      decided_at: now, reason: reason, can_approve: false, why_not: "Already decided.",
      result: approve ? {outbox_id: recOutbox || ("OB-DEMO-" + String(S.audit.entries.length + 1)), simulated: true} : null
    };
    updateEverywhere(id, patch);
    auditAppend({actor: me.user_id, event: approve ? "proposal_approved" : "proposal_rejected",
                 action: p.domain + "." + p.tool, resource: id, decision: "allow", reason: reason,
                 approval_id: id, details: {tier: p.tier}});
    if (approve) {
      auditAppend({actor: "svc:executor", event: "proposal_executed", action: p.domain + "." + p.tool,
                   resource: id, decision: "allow", approval_id: id,
                   details: {params_hash_rechecked: true}});
    }
    var resumed = resumedRec ? U.copy(resumedRec) : null;
    // As on the server (_runner.for_viewer): the resumed run belongs to the requester; an approver
    // sees its status and route, never the requester's thread content.
    if (resumed && !(p.requester && p.requester.user_id === me.user_id)) {
      resumed.output = {text: "", citations: [], data: {}};
      resumed.events = (resumed.events || []).filter(function (e) {
        return e.t === "route" || e.t === "approval" || e.t === "hwm" || e.t === "notice";
      });
    }
    return {proposal: U.copy(findProposal(key, id)), run: resumed};
  }

  function need(key, cap) {
    var me = meFor(key);
    if (!me) throw err(401, "unauthenticated", "Pick a persona first.");
    if (!me.can || !me.can[cap]) throw err(403, "forbidden", "Your role may not open this page.");
    return me;
  }

  function parseQuery(path) {
    var out = {};
    var i = path.indexOf("?");
    if (i < 0) return out;
    path.slice(i + 1).split("&").forEach(function (kv) {
      var parts = kv.split("=");
      out[decodeURIComponent(parts[0])] = decodeURIComponent(parts[1] || "");
    });
    return out;
  }

  function handle(method, path, body) {
    var bare = path.split("?")[0];
    var q = parseQuery(path);
    var key = S.persona;
    var m;

    if (method === "GET" && bare === "/api/health") {
      var h = U.copy(F.health || {ok: true, mode: "fake", sim_today: "2026-10-01"});
      h.switches = U.copy(S.switches);
      return h;
    }
    if (method === "GET" && bare === "/api/personas") return U.copy(personaList());
    if (method === "POST" && bare === "/api/auth/demo-login") {
      var p = personaList().filter(function (x) { return x.user_id === (body && body.user_id); })[0];
      if (!p) throw err(400, "bad_request", "Unknown persona.");
      S.persona = p.persona_key;
      U.store.set("demoPersona", p.persona_key);
      auditAppend({actor: p.user_id, event: "demo_login", decision: "allow", reason: "persona picker",
                   details: {persona_key: p.persona_key}});
      return {ok: true};
    }
    if (method === "POST" && bare === "/api/auth/logout") {
      S.persona = null;
      U.store.set("demoPersona", null);
      return {ok: true};
    }
    if (!key) throw err(401, "unauthenticated", "Pick a persona first.");

    if (method === "GET" && bare === "/api/me") return meFor(key);
    if (method === "POST" && bare === "/api/runs") return runAs(key, body || {});
    if (method === "GET" && (m = bare.match(/^\/api\/runs\/([^/]+)\/trace$/))) {
      var owner = runOwner(m[1]);
      var tr = F.traces && F.traces.by_run && F.traces.by_run[m[1]];
      // own runs only: someone else's run is indistinguishable from a missing one
      if (!tr || !(owner === key || owner === "*")) throw err(404, "not_found", "No such run among yours.");
      return U.copy(tr);
    }
    if (method === "GET" && bare === "/api/proposals") {
      var box = q.box === "to_approve" ? "to_approve" : "mine";
      var me = meFor(key);
      if (box === "to_approve" && !(me.can && me.can.approve)) return [];
      var list = boxes(key)[box] || [];
      // like the server: the approval inbox lists pending proposals only
      if (box === "to_approve") list = list.filter(function (p) { return p.status === "pending"; });
      return U.copy(list);
    }
    if (method === "POST" && (m = bare.match(/^\/api\/proposals\/([^/]+)\/(approve|reject)$/))) {
      return decide(key, m[1], body || {}, m[2] === "approve");
    }
    if (method === "GET" && bare === "/api/audit") {
      var aud = need(key, "audit");
      var after = parseInt(q.after_seq || "0", 10) || 0;
      var limit = Math.min(parseInt(q.limit || "200", 10) || 200, 1000);
      // every read of the log is itself audited (event audit_read)
      auditAppend({actor: aud.user_id, event: "audit_read", action: "read_log", decision: "allow",
                   details: {after_seq: after, limit: limit}});
      var rows = S.audit.entries.filter(function (e) { return e.seq > after; }).slice(0, limit);
      var chain = U.copy(S.audit.chain);
      if (S.tampered) {
        chain.ok = false;
        chain.first_bad_seq = rows.length > 3 ? rows[3].seq : (rows[0] ? rows[0].seq : 1);
      }
      return {entries: U.copy(rows), chain: chain};
    }
    if (bare === "/api/admin/switches") {
      var adm = need(key, "admin");
      if (method === "GET") return U.copy(S.switches);
      if (method === "POST") {
        var name = body && body.name;
        if (name !== "local_down" && name !== "external_down") throw err(400, "bad_request", "Unknown switch.");
        if (!String((body && body.reason) || "").trim()) throw err(400, "bad_request", "Give a reason for the switch.");
        S.switches[name] = !!body.on;
        auditAppend({actor: adm.user_id, event: "outage_switch", action: name, decision: "allow",
                     reason: String(body.reason), details: {on: !!body.on}});
        return U.copy(S.switches);
      }
    }
    throw err(404, "not_found", "Not available in demo mode.");
  }

  function canFind(key, app, input) {
    return !!findRun(key, {app: app, input: input});
  }

  KB.demo = {
    reload: reload,
    // Promise-based like fetch; a short delay keeps the UI's loading states honest.
    request: function (method, path, body) {
      return new Promise(function (resolve, reject) {
        setTimeout(function () {
          try { resolve(handle(method, path, body)); } catch (e) { reject(e); }
        }, method === "POST" && path === "/api/runs" ? 450 : 60);
      });
    },
    // Used by the comparison page: ask as another persona without changing the login.
    runAs: function (key, body) {
      return new Promise(function (resolve, reject) {
        setTimeout(function () {
          try { resolve(runAs(key, body)); } catch (e) { reject(e); }
        }, 300);
      });
    },
    restore: function () {
      var k = U.store.get("demoPersona");
      if (k && meFor(k)) S.persona = k;
    },
    suggestions: function (key, app) {
      var items = (F.runs && F.runs.items) || [];
      var seen = {};
      return items.filter(function (it) {
        if (!matches(it, key, app) || it.switches || seen[it.input]) return false;
        seen[it.input] = true;
        return true;
      }).map(function (it) { return it.input; });
    },
    // A recorded sample for the app page: the current persona's own recording first, then one
    // for everyone ("*"), then any. `runnable` says whether 运行 with its input would find a
    // recording for this persona (only then does the page pre-fill the input with it).
    sample: function (app, key) {
      var items = ((F.runs && F.runs.items) || []).filter(function (it) { return it.app === app && !it.switches; });
      var pick = items.filter(function (it) { return key && it.persona_key === key; })[0] ||
        items.filter(function (it) { return it.persona_key === "*"; })[0] || items[0];
      if (!pick) return null;
      var out = U.copy(pick);
      out.runnable = !!key && canFind(key, app, pick.input);
      return out;
    },
    // Whether the trace page would answer for this run as the current persona (own runs only).
    hasTrace: function (runId) {
      var owner = runOwner(runId);
      return !!(S.persona && F.traces && F.traces.by_run && F.traces.by_run[runId] &&
        (owner === S.persona || owner === "*"));
    },
    persona: function () { return S.persona; },
    setTampered: function (on) { S.tampered = !!on; },
    isTampered: function () { return S.tampered; },
    switches: function () { return U.copy(S.switches); }
  };
})();
