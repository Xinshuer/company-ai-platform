// Shared building blocks: route badge, level chip, status chips, citations, event timeline,
// the generic §18 run card, error and denial panels.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  function t(k, v) { return KB.lang.t(k, v); }

  var C = (KB.c = {});

  C.icon = function (name, cls) { return KB.icon(name, cls); };

  // local = amber 本地, external = blue 外部
  C.routeBadge = function (route) {
    if (route !== "local" && route !== "external") {
      return el("span", {class: "badge badge-muted"}, t("route_none"));
    }
    return el("span", {
      class: "badge route route-" + route,
      title: t(route === "local" ? "route_local_hint" : "route_external_hint")
    }, [C.icon(route === "local" ? "cpu" : "cloud", "icon-xs"), t("route_" + route)]);
  };

  // L1–L4 chip with a four-step meter, so the level reads without colour too
  C.levelChip = function (level, opts) {
    var n = parseInt(level, 10);
    if (!(n >= 1 && n <= 4)) n = 4;          // unknown level shows as the strictest (fail closed)
    opts = opts || {};
    var meter = el("span", {class: "lv-meter", "aria-hidden": "true"},
      [1, 2, 3, 4].map(function (i) { return el("span", {class: i <= n ? "on" : ""}); }));
    return el("span", {
      class: "chip level level-" + n + (opts.compact ? " chip-compact" : ""),
      title: t("level_" + n + "_hint")
    }, [opts.prefix ? el("span", {class: "chip-prefix"}, opts.prefix) : null, meter,
        el("span", {class: "lv-code"}, "L" + n),
        opts.compact ? null : el("span", {class: "lv-name"}, t("level_" + n))]);
  };

  C.hwmChip = function (level) {
    return C.levelChip(level, {prefix: t("hwm_short")});
  };

  C.statusChip = function (status) {
    var map = {done: "ok", ok: "ok", executed: "ok", approved: "ok", waiting_approval: "wait",
               pending: "wait", running: "wait", error: "bad", rejected: "bad", failed: "bad", expired: "muted"};
    var key = "status_" + status;
    return el("span", {class: "chip status status-" + (map[status] || "muted")},
      KB.lang.has(key) ? t(key) : String(status));
  };

  C.decisionChip = function (d) {
    if (!d) return el("span", {class: "muted"}, "—");
    var cls = d === "allow" ? "ok" : (d === "deny" ? "bad" : (d === "error" ? "bad" : "wait"));
    var key = "decision_" + d;
    return el("span", {class: "chip status status-" + cls}, KB.lang.has(key) ? t(key) : d);
  };

  C.tierBadge = function (tier) {
    var n = parseInt(tier, 10) || 0;
    return el("span", {class: "badge tier tier-" + n, title: t("tier_" + n + "_hint")}, t("tier_" + n));
  };

  C.mono = function (s, cls) { return el("code", {class: "mono" + (cls ? " " + cls : "")}, String(s)); };

  C.reasonText = function (reason) {
    reason = String(reason || "");
    var key = "reason_" + reason;
    if (KB.lang.has(key)) return t(key);
    // the runtime's hwm reasons carry a source: "retrieval:<purpose>", "tool:<domain>.<tool>"
    var i = reason.indexOf(":");
    if (i > 0) {
      var fam = reason.slice(0, i);
      if (fam === "retrieval") return t("reason_retrieval");
      if (fam === "tool") return t("reason_tool", {name: reason.slice(i + 1)});
    }
    return reason;
  };

  C.pageHeader = function (id, titleKey, subKey, extra) {
    return el("header", {class: "page-head"}, [
      el("div", {class: "page-head-text"}, [
        el("h1", {id: id, tabindex: "-1"}, t(titleKey)),
        subKey ? el("p", {class: "page-sub"}, t(subKey)) : null
      ]),
      extra ? el("div", {class: "page-head-extra"}, extra) : null
    ]);
  };

  C.panel = function (kind, iconName, title, body) {
    return el("div", {class: "panel panel-" + kind, role: kind === "bad" ? "alert" : null}, [
      C.icon(iconName, "panel-icon"),
      el("div", {class: "panel-body"}, [
        title ? el("p", {class: "panel-title"}, title) : null,
        body ? el("div", {class: "panel-text"}, body) : null
      ])
    ]);
  };

  C.denied = function (what) {
    return C.panel("bad", "lock", t("denied_title"), [el("p", {}, what || t("denied_generic"))]);
  };

  C.loading = function (label) {
    return el("div", {class: "loading", role: "status"}, [el("span", {class: "spinner", "aria-hidden": "true"}),
      el("span", {}, label || t("loading"))]);
  };

  C.errorBox = function (e) {
    var code = (e && e.code) || "unknown";
    var key = "err_" + code;
    var title = KB.lang.has(key) ? t(key) : t("err_unknown");
    var hintKey = "err_" + code + "_hint";
    var body = [];
    if (KB.lang.has(hintKey)) body.push(el("p", {}, t(hintKey)));
    var msg = e && (e.serverMessage || e.message);
    if (msg && msg !== code) body.push(el("p", {class: "server-msg"}, [el("span", {class: "muted"}, t("server_says") + " "), msg]));
    body.push(el("p", {class: "muted small"}, [t("error_code") + " ", C.mono(code)]));
    return C.panel("bad", code === "forbidden" ? "lock" : "alert", title, body);
  };

  C.copyButton = function (text) {
    var b = el("button", {class: "btn btn-ghost btn-xs", type: "button", "aria-label": t("copy")},
      [C.icon("copy", "icon-xs"), el("span", {}, t("copy"))]);
    b.addEventListener("click", function () {
      var done = function () { U.toast(t("copied"), "ok"); };
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { U.toast(t("copy_failed"), "bad"); });
        } else { U.toast(t("copy_failed"), "bad"); }
      } catch (e) { U.toast(t("copy_failed"), "bad"); }
    });
    return b;
  };

  C.jsonBlock = function (obj) {
    return el("pre", {class: "json", tabindex: "0"}, U.json(obj));
  };

  // ---------------------------------------------------------------- citations
  C.citations = function (cits) {
    cits = cits || [];
    if (!cits.length) {
      return el("div", {class: "citations empty"}, [el("p", {class: "muted small"}, t("no_citations"))]);
    }
    return el("div", {class: "citations"}, [
      el("h3", {class: "label"}, t("citations_n", {n: cits.length})),
      el("ol", {class: "cit-list"}, cits.map(function (c, i) {
        var lvl = parseInt(c.level, 10) || 4;
        return el("li", {class: "cit"}, [
          el("span", {class: "cit-num", "aria-hidden": "true"}, String(i + 1)),
          el("div", {class: "cit-main"}, [
            el("div", {class: "cit-head"}, [
              el("span", {class: "cit-title"}, c.title || c.doc_id),
              C.levelChip(lvl, {compact: true})
            ]),
            el("div", {class: "cit-meta"}, [C.mono(c.doc_id || "?", "small"),
              c.collection ? el("span", {class: "pill"}, KB.lang.has("coll_" + c.collection) ? t("coll_" + c.collection) : c.collection) : null]),
            lvl >= 4 || !c.snippet
              ? el("p", {class: "cit-snippet muted"}, [C.icon("eyeoff", "icon-xs"), lvl >= 4 ? t("metadata_only") : t("no_snippet")])
              : el("p", {class: "cit-snippet"}, c.snippet)
          ])
        ]);
      }))
    ]);
  };

  // ---------------------------------------------------------------- events
  var EV_ICON = {classify: "tag", retrieval: "database", tool: "tool", hwm: "trending", route: "route",
                 approval: "inbox", notice: "bell", citations: "check", policy: "lock"};

  C.eventLine = function (e) {
    var body;
    switch (e.t) {
      case "classify":
        body = [t("ev_classify", {level: e.level}),
          (e.findings || []).length ? el("span", {class: "ev-tags"}, e.findings.map(function (f) {
            return el("span", {class: "pill"}, KB.lang.has("finding_" + f) ? t("finding_" + f) : f);
          })) : el("span", {class: "muted"}, " " + t("ev_no_findings"))];
        break;
      case "retrieval":
        body = [t("ev_retrieval", {count: e.count, level: e.level}),
          el("span", {class: "ev-tags"}, (e.collections || []).map(function (c) {
            return el("span", {class: "pill"}, KB.lang.has("coll_" + c) ? t("coll_" + c) : c);
          }))];
        break;
      case "tool":
        body = [el("span", {class: "mono"}, (e.domain || "?") + "." + (e.tool || "?")), " ",
          C.decisionChip(e.decision),
          (e.denied || []).length ? el("ul", {class: "ev-denied"}, e.denied.map(function (d) {
            return el("li", {}, [C.mono(d.id || "?"), " — ", d.reason || ""]);
          })) : null];
        break;
      case "hwm":
        body = [t("ev_hwm", {from: e.from, to: e.to}), " ",
          el("span", {class: "muted"}, "(" + C.reasonText(e.reason) + ")")];
        break;
      case "route":
        body = [t("ev_route"), " ", C.routeBadge(e.route), " ",
          el("span", {class: "muted"}, C.reasonText(e.reason))];
        break;
      case "approval":
        body = [t("ev_approval", {tier: e.tier}), " ", C.mono(e.proposal_id || "?")];
        break;
      case "notice":
        body = [KB.lang.pick(e) || e.code];
        break;
      case "citations":
        body = [t("ev_citations", {kept: e.kept || 0, dropped: e.dropped || 0})];
        break;
      case "policy":
        body = e.code === "tool_not_offered"
          ? [t("ev_policy_tool_not_offered", {tool: e.tool || "?"}), " ", C.decisionChip(e.decision || "deny")]
          : [C.mono(e.code || "?"), " ", C.decisionChip(e.decision || "deny")];
        break;
      default:
        body = [C.mono(U.json(e))];
    }
    var lvl = e.level !== undefined && e.t !== "hwm" ? C.levelChip(e.level, {compact: true}) : null;
    return el("li", {class: "ev ev-" + (e.t || "other") + (e.decision === "deny" ? " ev-deny" : "")}, [
      el("span", {class: "ev-dot", "aria-hidden": "true"}, [C.icon(EV_ICON[e.t] || "info", "icon-xs")]),
      el("div", {class: "ev-body"}, [
        el("span", {class: "ev-type"}, KB.lang.has("evt_" + e.t) ? t("evt_" + e.t) : String(e.t)),
        el("div", {class: "ev-text"}, body)
      ]),
      lvl
    ]);
  };

  C.timeline = function (events, open) {
    events = events || [];
    var d = el("details", {class: "timeline"});
    if (open) d.open = true;
    d.appendChild(el("summary", {}, [C.icon("activity", "icon-xs"), t("timeline_n", {n: events.length})]));
    d.appendChild(el("ol", {class: "ev-list"}, events.map(C.eventLine)));
    return d;
  };

  C.deniedTools = function (events) {
    return (events || []).filter(function (e) { return e.t === "tool" && e.decision === "deny"; });
  };

  // ---------------------------------------------------------------- output.data renderers
  function resultChip(r) {
    var cls = r === "met" ? "ok" : (r === "not_met" ? "bad" : "muted");
    return el("span", {class: "chip status status-" + cls}, t("req_" + (r || "unknown")));
  }

  function table(headers, rows) {
    return el("div", {class: "table-wrap"}, [el("table", {class: "table"}, [
      el("thead", {}, [el("tr", {}, headers.map(function (h) { return el("th", {scope: "col"}, h); }))]),
      el("tbody", {}, rows.map(function (r) {
        return el("tr", {}, r.map(function (cell) { return el("td", {}, cell); }));
      }))
    ])]);
  }

  function scalar(v) {
    if (v === null || v === undefined) return el("span", {class: "muted"}, "—");
    if (typeof v === "object") return C.mono(JSON.stringify(v), "small");
    return String(v);
  }

  C.dataView = function (data) {
    if (!data || typeof data !== "object" || !Object.keys(data).length) return null;
    var parts = [];
    if (Array.isArray(data.requirements)) {
      parts.push(el("h3", {class: "label"}, t("data_requirements")));
      parts.push(table([t("col_requirement"), t("col_result"), t("col_evidence")], data.requirements.map(function (r) {
        return [r.requirement, resultChip(r.result), r.quote ? el("q", {}, r.quote) : el("span", {class: "muted"}, t("no_evidence"))];
      })));
      if (data.removed) {
        parts.push(el("p", {class: "small muted"}, t("data_removed", {list: data.removed.join(", ")})));
      }
      parts.push(el("p", {class: "small"}, [C.icon("info", "icon-xs"), t("data_no_score")]));
    }
    if (Array.isArray(data.metrics)) {
      parts.push(el("h3", {class: "label"}, t("data_metrics")));
      parts.push(el("div", {class: "stat-grid"}, data.metrics.map(function (m) {
        var v = typeof m.value === "number" ? m.value.toLocaleString(KB.lang.current === "zh" ? "zh-CN" : "en-GB") : String(m.value);
        return el("div", {class: "stat"}, [
          el("span", {class: "stat-label"}, m.label || m.id),
          el("span", {class: "stat-value"}, [v, m.unit ? el("span", {class: "stat-unit"}, " " + m.unit) : null]),
          el("span", {class: "stat-foot"}, [C.mono(m.id, "small"), " ", C.levelChip(m.level, {compact: true})])
        ]);
      })));
    }
    if (typeof data.fit_score === "number" || typeof data.intent_score === "number") {
      parts.push(el("h3", {class: "label"}, t("data_scores")));
      parts.push(el("div", {class: "meters"}, [["fit_score", "score_fit"], ["intent_score", "score_intent"]].map(function (s) {
        var v = Math.max(0, Math.min(100, Number(data[s[0]]) || 0));
        return el("div", {class: "meter"}, [
          el("div", {class: "meter-head"}, [el("span", {}, t(s[1])), el("strong", {}, String(v))]),
          el("div", {class: "meter-track", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(v), "aria-label": t(s[1])},
            [el("span", {class: "meter-fill", style: "width:" + v + "%"})]),
          el("p", {class: "small muted"}, t(s[1] + "_hint"))
        ]);
      })));
    }
    if (Array.isArray(data.findings)) {
      parts.push(el("h3", {class: "label"}, t("data_findings")));
      parts.push(table([t("col_clause"), t("col_issue"), t("col_severity"), t("col_regulation")], data.findings.map(function (f) {
        return [f.clause, f.issue, el("span", {class: "chip sev sev-" + (f.severity || "info")}, f.severity || "info"), scalar(f.regulation)];
      })));
    }
    if (Array.isArray(data.headcount)) {
      parts.push(el("h3", {class: "label"}, t("data_headcount")));
      var max = Math.max.apply(null, data.headcount.map(function (h) { return h.n || 0; }).concat([1]));
      parts.push(el("div", {class: "bars"}, data.headcount.map(function (h) {
        return el("div", {class: "bar-row"}, [
          el("span", {class: "bar-label"}, KB.lang.has("dept_" + h.dept) ? t("dept_" + h.dept) : h.dept),
          el("span", {class: "bar-track"}, [el("span", {class: "bar-fill", style: "width:" + Math.round(100 * (h.n || 0) / max) + "%"})]),
          el("span", {class: "bar-val"}, String(h.n))
        ]);
      })));
    }
    var known = {requirements: 1, removed: 1, decision: 1, metrics: 1, fit_score: 1, intent_score: 1,
                 findings: 1, headcount: 1};
    var rest = {};
    Object.keys(data).forEach(function (k) { if (!known[k]) rest[k] = data[k]; });
    if (Object.keys(rest).length) {
      parts.push(el("h3", {class: "label"}, t("data_other")));
      parts.push(el("dl", {class: "kv"}, Object.keys(rest).reduce(function (acc, k) {
        acc.push(el("dt", {}, k));
        acc.push(el("dd", {}, scalar(rest[k])));
        return acc;
      }, [])));
    }
    return el("div", {class: "data-view"}, parts);
  };

  // Whether "view trace" can work for this run: the trace API answers for the caller's own runs
  // only, so the link is left out for samples, other personas' compare columns and a resumed run
  // shown to its approver (pass opts.trace = false), and in demo for runs without a recording.
  C.canTrace = function (run, opts) {
    if (!run || !run.run_id || (opts && opts.trace === false)) return false;
    return KB.api.mode !== "demo" || KB.demo.hasTrace(run.run_id);
  };

  // Model text written by the offline fake model (tests, `CORPAI_LLM=fake`, and every demo recording
  // so far: issues-log D-07) starts with "[fake/<route>]". Say so next to it, so nobody takes the
  // wording for what the real model writes. Demo recordings that hold no such tag were made the same
  // way (or written by hand), so in demo mode the note is shown whenever a run called a model.
  C.isFakeText = function (text) { return /^\s*\[fake\//.test(String(text || "")); };
  C.fakeModelNote = function (run) {
    var text = run && run.output && run.output.text;
    var demo = KB.api.mode === "demo" && run && run.route;
    if (!C.isFakeText(text) && !demo) return null;
    return el("p", {class: "small muted fake-note", role: "note"}, [C.icon("info", "icon-xs"), t("fake_model_note")]);
  };

  // ---------------------------------------------------------------- the §18 run card
  C.runCard = function (run, opts) {
    opts = opts || {};
    if (!run) return null;
    var head = el("div", {class: "run-head"}, [
      C.statusChip(run.status),
      C.routeBadge(run.route),
      C.hwmChip(run.hwm),
      opts.persona ? el("span", {class: "run-who"}, opts.persona) : null,
      el("span", {class: "spacer"}),
      C.canTrace(run, opts) ? el("a", {class: "link-btn", href: "#/trace/" + encodeURIComponent(run.run_id)},
        [C.icon("activity", "icon-xs"), t("view_trace")]) : null
    ]);
    var body = [];
    if (run.status === "error" && run.error) {
      body.push(C.errorBox({code: run.error.code, serverMessage: run.error.message}));
    }
    var text = run.output && run.output.text;
    if (text) body.push(el("div", {class: "answer"}, U.linkify(text)));
    if (text) body.push(C.fakeModelNote(run));
    if (run.pending_approval) {
      var pa = run.pending_approval;
      body.push(C.panel("wait", "inbox", t("pending_title"), [
        el("p", {}, [C.mono(pa.tool), " ", C.tierBadge(pa.tier)]),
        el("p", {}, pa.summary || ""),
        el("p", {}, [el("a", {href: "#/approvals", class: "link-btn"}, [t("open_approvals"), C.icon("chevron", "icon-xs")])])
      ]));
    }
    var dv = C.dataView(run.output && run.output.data);
    if (dv) body.push(dv);
    if (!opts.noCitations) body.push(C.citations(run.output && run.output.citations));
    body.push(C.timeline(run.events, opts.openTimeline));
    body.push(el("p", {class: "run-id small muted"}, [t("run_id") + " ", C.mono(run.run_id || "—", "small"),
      run.thread_id ? [" · " + t("thread_id") + " ", C.mono(run.thread_id, "small")] : null]));
    return el("article", {class: "card run-card run-" + (run.status || "x")}, [head, el("div", {class: "run-body"}, body)]);
  };

  C.localBanner = function () {
    return el("div", {class: "local-banner", role: "status"}, [
      C.icon("cpu", "icon-sm"),
      el("div", {}, [el("strong", {}, t("local_banner")), el("span", {class: "small"}, " " + t("local_banner_hint"))])
    ]);
  };

  C.avatar = function (name, dept, size) {
    return el("span", {class: "avatar avatar-" + (size || "md") + " dept-" + (dept || "x"), "aria-hidden": "true"},
      U.initials(name));
  };

  C.roleBadge = function (role) {
    return el("span", {class: "badge role role-" + role}, t("role_" + role));
  };
})();
