// Account profile (M4, app sales_profile, route #/sales). Owned by the M4 sales work.
// What the page shows (design-review 二.6, decision D5): the company profile from public pages
// (fixed fields, every source URL in full, external links confirm first), the fit and intent
// scores computed by code with their reasons, the playbook tier, a route badge per step (web
// extraction external, intent in code, strategy local), pages flagged for embedded instructions,
// and the "draft follow-up e-mail" action that files a tier-3 proposal for another person to approve.
// Web page text never reaches this page. Everything from the API is rendered as text (KB.util.el).
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var APP = "sales_profile";
  var PROFILE_FIELDS = ["company", "domain", "industry", "hq_country", "countries", "fleet_size_band",
                        "employees_band", "funding_stage", "last_funding_eur", "growth_signals", "tech_signals"];

  // Page state survives navigation, never a change of persona (another user must not see it).
  function st() {
    var me = KB.state.me || {};
    var s = KB.state.sales;
    if (!s || s.owner !== me.user_id) {
      s = KB.state.sales = {owner: me.user_id, run: null, followup: null, accounts: null};
    }
    return s;
  }

  function num(v) {
    return typeof v === "number" ? v.toLocaleString(KB.lang.current === "zh" ? "zh-CN" : "en-GB") : String(v);
  }

  function list(items) {
    if (!items || !items.length) return el("span", {class: "muted"}, "—");
    return el("ul", {class: "steps"}, items.map(function (x) { return el("li", {}, String(x)); }));
  }

  // A step's route and level. The intent step reports L3 (the CRM activity it reads), but when that
  // read was refused it read nothing, so it shows the refusal instead of a level (issues-log WR-18).
  function stepBadge(s, data) {
    var route = s.route === "code"
      ? el("span", {class: "badge badge-muted", title: t("sales_route_code_hint")}, [C.icon("cpu", "icon-xs"), t("sales_route_code")])
      : C.routeBadge(s.route);
    var label = KB.lang.has("sales_step_" + s.step) ? t("sales_step_" + s.step) : String(s.step);
    var refused = s.step === "intent" && ((data && data.intent) || {}).status !== "ok";
    return el("span", {class: "chip"}, [el("strong", {}, label), " ", route, " ", refused
      ? el("span", {class: "chip status status-bad chip-compact"}, [C.icon("lock", "icon-xs"), t("sales_step_refused")])
      : C.levelChip(s.level, {compact: true})]);
  }

  // Codes of the extraction schema (corpai/sales/profile.py) in the page's language; bands and
  // country codes read the same in both.
  var CODED = {industry: "sales_ind_", funding_stage: "sales_fund_", growth_signals: "sales_sig_", tech_signals: "sales_sig_"};

  function codeLabel(k, v) {
    var key = CODED[k] ? CODED[k] + v : null;
    return key && KB.lang.has(key) ? t(key) : String(v);
  }

  // A scoring reason of corpai/sales/scoring.py ("industry:parcel_delivery +25", "recency:12d +20")
  // in words; the code itself stays in the tooltip.
  var REASON_CODES = {industry: "industry", funding: "funding_stage"};

  function reasonList(items) {
    if (!items || !items.length) return el("span", {class: "muted"}, "—");
    return el("ul", {class: "steps"}, items.map(function (r) {
      var m = /^([a-z_]+):(.*) (\+\d+)$/.exec(String(r));
      if (!m || !KB.lang.has("sales_reason_" + m[1])) return el("li", {}, C.mono(String(r), "small"));
      var v = m[2] === "none" ? t("sales_reason_none") : (REASON_CODES[m[1]] ? codeLabel(REASON_CODES[m[1]], m[2]) : m[2]);
      return el("li", {title: String(r)}, t("sales_reason_fmt", {what: t("sales_reason_" + m[1]), value: v, pts: m[3]}));
    }));
  }

  function runHead(run) {
    return el("div", {class: "run-head"}, [
      C.statusChip(run.status), C.routeBadge(run.route), C.hwmChip(run.hwm),
      el("span", {class: "spacer"}),
      C.canTrace(run) ? el("a", {class: "link-btn", href: "#/trace/" + encodeURIComponent(run.run_id)},
        [C.icon("activity", "icon-xs"), t("view_trace")]) : null
    ]);
  }

  function refusedPanel(code) {
    var key = "sales_refused_" + code;
    return C.panel("info", "info", t("sales_refused_title"), [el("p", {}, KB.lang.has(key) ? t(key) : String(code))]);
  }

  function flaggedPanel(web) {
    var urls = (web && web.flagged_urls) || [];
    if (!urls.length) return null;
    return C.panel("wait", "alert", t("sales_flagged_title", {n: urls.length}), [
      el("p", {}, t("sales_flagged_text")),
      el("ul", {class: "steps"}, urls.map(function (u) { return el("li", {}, U.extLink(u)); }))
    ]);
  }

  function fieldValue(k, v) {
    if (v === null || v === undefined || v === "" || v === "unknown" || (Array.isArray(v) && !v.length)) {
      return el("span", {class: "muted"}, t("sales_unknown"));
    }
    if (k === "last_funding_eur") return num(v) + " EUR";
    if (Array.isArray(v)) {
      return el("span", {class: "ev-tags"}, v.map(function (x) { return el("span", {class: "pill"}, codeLabel(k, x)); }));
    }
    if (k === "company") return el("strong", {}, String(v));
    if (CODED[k]) return el("span", {title: String(v)}, codeLabel(k, v));
    return C.mono(String(v), "small");
  }

  function profileCard(data) {
    var p = data.profile;
    var web = data.web || {};
    if (!p) {
      return el("div", {class: "card pad"}, [el("h2", {class: "card-title"}, t("sales_profile_title")),
        C.panel("muted", "info", t("sales_web_failed"), [el("p", {class: "small"}, C.mono(String(web.error || "—"), "small"))])]);
    }
    var rows = [];
    PROFILE_FIELDS.forEach(function (k) {
      rows.push(el("dt", {}, t("sales_f_" + k)));
      rows.push(el("dd", {}, fieldValue(k, p[k])));
    });
    var sources = p.sources || [];
    return el("div", {class: "card pad"}, [
      el("h2", {class: "card-title"}, [t("sales_profile_title"), " ", C.levelChip(1, {compact: true})]),
      el("p", {class: "small muted"}, [C.icon("shield", "icon-xs"), t("sales_profile_hint")]),
      el("dl", {class: "kv"}, rows),
      el("h3", {class: "label"}, t("sales_sources_n", {n: sources.length})),
      sources.length ? el("ul", {class: "steps"}, sources.map(function (u) { return el("li", {}, U.extLink(u)); }))
        : el("p", {class: "muted small"}, t("sales_no_sources")),
      el("p", {class: "small muted"}, [
        t(web.public_only ? "sales_public_only_yes" : "sales_public_only_no"), " · ",
        t("sales_pages_used", {n: (web.pages_used || []).length}),
        (web.dropped_fields || []).length ? [" · ", t("sales_dropped", {list: web.dropped_fields.join(", ")})] : null
      ])
    ]);
  }

  function meter(label, hint, value) {
    var v = Math.max(0, Math.min(100, Number(value) || 0));
    return el("div", {class: "meter"}, [
      el("div", {class: "meter-head"}, [el("span", {}, label), el("strong", {}, String(v))]),
      el("div", {class: "meter-track", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100",
                 "aria-valuenow": String(v), "aria-label": label}, [el("span", {class: "meter-fill", style: "width:" + v + "%"})]),
      el("p", {class: "small muted"}, hint)
    ]);
  }

  function scoresCard(data) {
    var fit = data.fit;
    var intent = data.intent || {};
    var parts = [el("h2", {class: "card-title"}, t("sales_scores_title")),
                 el("p", {class: "small muted"}, t("sales_scores_hint"))];
    if (fit) {
      parts.push(meter(t("score_fit"), t("score_fit_hint"), fit.score));
      parts.push(el("details", {}, [el("summary", {class: "small"}, t("sales_reasons")), reasonList(fit.reasons)]));
    } else {
      parts.push(C.panel("muted", "info", t("score_fit"), [el("p", {}, t("sales_fit_missing"))]));
    }
    if (intent.status === "ok") {
      parts.push(meter(t("score_intent"), t("score_intent_hint"), intent.score));
      parts.push(el("details", {}, [el("summary", {class: "small"}, t("sales_reasons")), reasonList(intent.reasons)]));
    } else {
      parts.push(C.denied(t("sales_intent_denied")));
    }
    return el("div", {class: "card pad"}, parts);
  }

  function playbookCard(data) {
    var pb = data.playbook;
    if (!pb) return null;
    return el("div", {class: "card pad"}, [
      el("h2", {class: "card-title"}, [t("sales_playbook_title"), " ",
        el("span", {class: "pill pill-strong"}, t("sales_tier_" + (data.tier || "low"))), " ",
        C.levelChip(3, {compact: true})]),
      el("p", {class: "small muted"}, [t("sales_playbook_ref"), " ", C.mono(pb.ref, "small")]),
      el("dl", {class: "kv"}, [
        el("dt", {}, t("sales_pb_goal")), el("dd", {}, String(pb.goal || "")),
        el("dt", {}, t("sales_pb_cadence")),
        el("dd", {}, t("sales_pb_cadence_val", {d: pb.cadence_days, n: pb.max_touches_per_month})),
        el("dt", {}, t("sales_pb_channels")), el("dd", {}, (pb.channels || []).join(", ")),
        el("dt", {}, t("sales_pb_next")), el("dd", {}, list(pb.next_steps)),
        el("dt", {}, t("sales_pb_allowed")), el("dd", {}, list(pb.allowed_commitments)),
        el("dt", {}, t("sales_pb_approval")), el("dd", {}, list(pb.needs_approval)),
        el("dt", {}, t("sales_pb_forbidden")), el("dd", {}, list(pb.forbidden_commitments))
      ])
    ]);
  }

  function strategyCard(run, data) {
    var step = (data.steps || []).filter(function (s) { return s.step === "strategy"; })[0];
    if (!step) return null;
    return el("div", {class: "card pad"}, [
      el("h2", {class: "card-title"}, [t("sales_strategy_title"), " ", C.routeBadge(step.route)]),
      el("div", {class: "answer"}, String((run.output && run.output.text) || "")),
      C.fakeModelNote(run),
      el("p", {class: "small muted"}, t("sales_strategy_hint"))
    ]);
  }

  function renderFollowup(run) {
    var data = (run.output && run.output.data) || {};
    var body = [runHead(run)];
    if (run.status === "error" && run.error) body.push(C.errorBox({code: run.error.code, serverMessage: run.error.message}));
    if (data.refused) body.push(refusedPanel(data.refused));
    var d = data.draft;
    if (d && d.subject) {
      body.push(el("div", {class: "card pad"}, [
        el("h3", {class: "label"}, [t("sales_draft_title"), " ",
          el("span", {class: "badge badge-muted"}, t(d.source === "template" ? "sales_draft_template" : "sales_draft_model"))]),
        el("dl", {class: "kv"}, [
          el("dt", {}, t("sales_draft_to")), el("dd", {}, [String(d.contact_name || ""), " ", C.mono(d.contact_id || "", "small")]),
          el("dt", {}, t("sales_draft_subject")), el("dd", {}, String(d.subject)),
          el("dt", {}, t("sales_draft_body")), el("dd", {}, el("div", {class: "answer"}, String(d.body || "")))
        ])
      ]));
    }
    if (run.pending_approval) {
      var pa = run.pending_approval;
      body.push(C.panel("wait", "inbox", t("pending_title"), [
        el("p", {}, [C.mono(pa.tool || "?"), " ", C.tierBadge(pa.tier)]),
        el("p", {}, String(pa.summary || "")),
        el("p", {class: "small"}, t("sales_four_eyes")),
        el("p", {}, [el("a", {href: "#/approvals", class: "link-btn"}, [t("open_approvals"), C.icon("chevron", "icon-xs")])])
      ]));
    }
    if (data.decision) {
      body.push(C.panel(data.decision.approved ? "info" : "bad", data.decision.approved ? "checkcircle" : "x",
        t(data.decision.approved ? "sales_sent" : "sales_not_sent", {status: data.decision.status || "—"})));
    }
    body.push(C.timeline(run.events, false));
    return el("article", {class: "card run-card run-" + (run.status || "x")}, [el("div", {class: "run-body"}, body)]);
  }

  function followupCard(run, data) {
    if (!(data.followup && data.followup.available)) return null;
    var selId = U.uid("sales-contact");
    // demo: only some follow-ups are recorded; those come first and are selected, the rest are marked
    var contacts = (data.contacts || []).map(function (c) {
      return {c: c, ok: KB.appPage.recorded(APP, "/followup " + c.contact_id, run.thread_id)};
    });
    contacts.sort(function (a, b) { return (b.ok ? 1 : 0) - (a.ok ? 1 : 0); });
    var sel = el("select", {id: selId, class: "input input-sm"}, contacts.map(function (x) {
      var c = x.c;
      var label = String(c.name || c.contact_id) + (c.title ? " · " + c.title : "");
      return el("option", {value: c.contact_id, disabled: !x.ok, "data-unrecorded": x.ok ? null : "1"},
        x.ok ? label : label + t("demo_unrecorded_suffix"));
    }));
    var firstOk = contacts.filter(function (x) { return x.ok; })[0];
    if (firstOk) sel.value = firstOk.c.contact_id;
    var btn = el("button", {class: "btn btn-primary", type: "button"}, [C.icon("send", "icon-sm"), t("sales_draft_btn")]);
    if (!firstOk) KB.appPage.markUnrecorded(btn, APP, "/followup", run.thread_id);
    var out = el("div", {class: "result-slot", "aria-live": "polite"});
    btn.addEventListener("click", function () {
      btn.disabled = true;
      U.clear(out).appendChild(C.loading(t("running")));
      // the same conversation as the profile: the follow-up reads only its account and tier
      KB.api.post("/api/runs", {app: APP, input: "/followup " + sel.value, thread_id: run.thread_id, user_level: 1})
        .then(function (r) {
          st().followup = r;
          KB.app.rememberRun(r, t("sales_draft_btn"));
          U.clear(out).appendChild(renderFollowup(r));
        })
        .catch(function (e) {
          if (e.status === 401) return KB.app.sessionLost();
          U.clear(out).appendChild(C.errorBox(e));
        })
        .then(function () { btn.disabled = false; });
    });
    var f = st().followup;
    if (f && f.thread_id === run.thread_id) out.appendChild(renderFollowup(f));
    return el("div", {class: "card pad"}, [
      el("h2", {class: "card-title"}, t("sales_followup_title")),
      el("p", {class: "small"}, [C.tierBadge(3), " ", t("sales_followup_hint")]),
      el("div", {class: "form-row"}, [el("label", {for: selId, class: "small"}, t("sales_contact")), sel, btn]),
      out
    ]);
  }

  function renderProfile(run) {
    var data = (run.output && run.output.data) || {};
    if (data.kind === "sales_followup") return renderFollowup(run);
    if (data.kind !== "sales_profile") return C.runCard(run, {openTimeline: true});  // older recordings
    var body = [runHead(run)];
    if (run.status === "error" && run.error) {
      body.push(C.errorBox({code: run.error.code, serverMessage: run.error.message}));
    }
    if (data.refused) {
      body.push(refusedPanel(data.refused));
    } else if (run.status !== "error") {
      body.push(el("p", {class: "small"}, [t("sales_steps"), " "].concat((data.steps || []).map(function (s) {
        return [stepBadge(s, data), " "];
      }))));
      body.push(flaggedPanel(data.web));
      if (data.account) {
        body.push(el("p", {class: "small"}, [t("sales_account"), " ", el("strong", {}, String(data.account.name || "")),
          " ", C.mono(data.account.account_id || "", "small"), " · ",
          KB.lang.has("sales_status_" + data.account.status) ? t("sales_status_" + data.account.status) : String(data.account.status || "")]));
      }
      body.push(el("div", {class: "dept-grid"}, [profileCard(data), scoresCard(data)]));
      body.push(playbookCard(data));
      body.push(strategyCard(run, data));
      if (!data.playbook) body.push(el("p", {class: "small muted"}, t("sales_partial")));
      body.push(followupCard(run, data));
    }
    body.push(C.timeline(run.events, false));
    body.push(el("p", {class: "run-id small muted"}, [t("run_id") + " ", C.mono(run.run_id || "—", "small"),
      run.thread_id ? [" · " + t("thread_id") + " ", C.mono(run.thread_id, "small")] : null]));
    return el("article", {class: "card run-card run-" + (run.status || "x")}, [el("div", {class: "run-body"}, body)]);
  }

  // "Recorded examples" chips, as on the report and legal pages: a click fills the input.
  function chips(list, input) {
    if (!list.length) return null;
    return el("p", {class: "small", "data-recorded": "1"}, [t("demo_recorded_inputs") + " "].concat(list.map(function (s) {
      var b = el("button", {class: "chip chip-btn", type: "button"}, s);
      b.addEventListener("click", function () { input.value = s; input.focus(); });
      return [b, " "];
    })));
  }

  // Demo mode: the companies this persona has a recorded profile of (never a follow-up step).
  function recordedInputs() {
    var key = KB.api.mode === "demo" ? KB.demo.persona() : null;
    return (key ? KB.demo.suggestions(key, APP) : []).filter(function (s) { return !/^\/followup/i.test(s); });
  }

  function loadAccounts(datalist) {
    var s = st();
    function fill(rows) {
      U.clear(datalist);
      // demo: the recorded companies first, then the persona's accounts, unrecorded ones marked
      var seen = {};
      recordedInputs().forEach(function (name) {
        seen[name] = 1;
        datalist.appendChild(el("option", {value: name}, t("demo_recorded_inputs")));
      });
      (rows || []).forEach(function (a) {
        var name = String(a.name || "");
        if (seen[name]) return;
        var ok = KB.appPage.recorded(APP, name);
        datalist.appendChild(el("option", {value: name, "data-unrecorded": ok ? null : "1"},
          String(a.account_id || "") + (ok ? "" : t("demo_unrecorded_suffix"))));
      });
    }
    if (s.accounts) { fill(s.accounts); return; }
    KB.api.get("/api/sales/accounts").then(function (r) {
      s.accounts = (r && r.accounts) || [];
      fill(s.accounts);
    }, function () { /* a convenience only: typing a name still works */ });
  }

  KB.views = KB.views || {};
  KB.views.sales = {
    render: function (root) {
      root.appendChild(C.pageHeader("h-sales", "app_sales_profile", "app_sales_profile_desc",
        [el("span", {class: "pill pill-strong"}, t("milestone_pill", {m: "M4"}))]));
      if (!KB.appPage.hasApp(APP)) {
        KB.appPage.denied(root, APP);  // explicit denial (ENGINEERING.md rule 6)
        return;
      }
      var s = st();
      root.appendChild(C.panel("info", "shield", t("sales_notice_title"), [
        el("p", {}, t("app_sales_profile_safety")), el("p", {class: "small"}, t("sales_notice_text"))]));
      KB.appPage.demoCoverage(root, APP);  // demo: say up front when this persona has no recording

      var inputId = U.uid("sales-company");
      var listId = U.uid("sales-accounts");
      var datalist = el("datalist", {id: listId});
      var input = el("input", {id: inputId, class: "input", type: "text", maxlength: "100", list: listId,
                               autocomplete: "off", placeholder: t("sales_input_placeholder")});
      var btn = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("search", "icon-sm"), t("sales_run")]);
      var slot = el("div", {class: "result-slot", "aria-live": "polite"});
      var form = el("form", {class: "card pad form-inline", novalidate: true}, [
        el("label", {for: inputId, class: "label"}, t("app_sales_profile_input")),
        el("div", {class: "form-row"}, [input, btn]), datalist,
        el("p", {class: "small muted"}, t("sales_input_hint")),
        chips(recordedInputs(), input)
      ]);
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var text = input.value.trim();
        if (!text) { input.focus(); return; }
        btn.disabled = true;
        U.clear(slot).appendChild(C.loading(t("sales_running")));
        // a new conversation per company, so a follow-up e-mail belongs to exactly this profile
        KB.api.post("/api/runs", {app: APP, input: text, thread_id: null, user_level: 1}).then(function (run) {
          s.run = run;
          s.followup = null;
          KB.app.rememberRun(run, text);
          U.clear(slot).appendChild(renderProfile(run));
        }).catch(function (e) {
          if (e.status === 401) return KB.app.sessionLost();
          U.clear(slot).appendChild(C.errorBox(e));
        }).then(function () { btn.disabled = false; });
      });
      root.appendChild(form);
      root.appendChild(slot);
      loadAccounts(datalist);
      if (s.run) slot.appendChild(renderProfile(s.run));
    }
  };
})();
