// HR apps (M4), owned by the M4 HR work:
// - #/hr          résumé screening (hr_screening): evidence per hard requirement with verbatim quotes,
//                 the protected-attribute removal note, the EU AI Act / GDPR Art. 22 notice, and the
//                 HR person's decision buttons, which need a reason. There is no score anywhere.
// - #/headcount   headcount (hr_headcount): the public part (L2) and the budget part (L3, only for
//                 those entitled), denials listed, groups under 5 hidden.
// - #/candidates  the hiring manager's read-only candidate list (hr_candidates; issues-log D-14).
// Résumé text and model output are untrusted: rendered as text only (KB.util.el), never as markup.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var DECISIONS = ["advance", "hold", "reject"];
  var NOT_HM = "you are not the hiring manager or recruiter of any requisition";

  // Per-page state that survives navigation but never a change of persona.
  function st(name) {
    var me = KB.state.me || {};
    var key = "hr_" + name;
    var s = KB.state[key];
    if (!s || s.owner !== me.user_id) s = KB.state[key] = {owner: me.user_id};
    return s;
  }

  function header(root, route, app) {
    root.appendChild(C.pageHeader("h-" + route, "app_" + app, "app_" + app + "_desc",
      [el("span", {class: "pill pill-strong"}, t("milestone_pill", {m: "M4"}))]));
    if (!KB.appPage.hasApp(app)) {
      KB.appPage.denied(root, app);  // explicit denial (ENGINEERING.md rule 6)
      return false;
    }
    return true;
  }

  function runHead(run) {
    return el("div", {class: "run-head"}, [
      C.statusChip(run.status), C.routeBadge(run.route), C.hwmChip(run.hwm),
      el("span", {class: "spacer"}),
      C.canTrace(run) ? el("a", {class: "link-btn", href: "#/trace/" + encodeURIComponent(run.run_id)},
        [C.icon("activity", "icon-xs"), t("view_trace")]) : null
    ]);
  }

  function failed(slot, e) {
    if (e.status === 401) return KB.app.sessionLost();
    U.clear(slot).appendChild(C.errorBox(e));
  }

  function table(headers, rows) {
    return el("div", {class: "table-wrap"}, [el("table", {class: "table"}, [
      el("thead", {}, [el("tr", {}, headers.map(function (h) { return el("th", {scope: "col"}, h); }))]),
      el("tbody", {}, rows.map(function (r) {
        return el("tr", {}, r.map(function (cell) { return el("td", {}, cell); }));
      }))
    ])]);
  }

  function deptName(d) {
    return d ? (KB.lang.has("dept_" + d) ? t("dept_" + d) : String(d)) : t("hr_no_permission");
  }

  function translated(prefix, code) {
    return KB.lang.has(prefix + code) ? t(prefix + code) : String(code);
  }

  function deniedReason(d) {
    return d.reason === NOT_HM ? t("hr_not_hiring_manager") : (d.reason === "no permission" ? t("hr_no_permission") : String(d.reason || ""));
  }

  // ---------------------------------------------------------------- résumé (untrusted text)

  function resumeBlock(candidateId, app) {
    var slot = el("div", {class: "result-slot"});
    var btn = el("button", {class: "btn btn-outline btn-sm", type: "button"}, [C.icon("doc", "icon-xs"), t("hr_show_resume")]);
    btn.addEventListener("click", function () {
      btn.disabled = true;
      U.clear(slot).appendChild(C.loading(t("loading")));
      KB.api.get("/api/hr/candidates/" + encodeURIComponent(candidateId) + "?app=" + app).then(function (r) {
        var c = (r && r.candidate) || {};
        U.clear(slot).appendChild(C.panel("muted", "eyeoff", t("hr_resume_title", {id: String(c.candidate_id || candidateId)}), [
          el("p", {class: "small"}, [C.levelChip(r.level, {compact: true}), " ", t("hr_resume_untrusted")]),
          el("div", {class: "answer"}, String(c.resume_text || ""))
        ]));
      }, function (e) { failed(slot, e); }).then(function () { btn.disabled = false; });
    });
    return el("div", {}, [btn, slot]);
  }

  // ---------------------------------------------------------------- screening (#/hr)

  function verdictChip(v) {
    var cls = v === "met" ? "ok" : (v === "not_met" ? "bad" : "muted");
    return el("span", {class: "chip status status-" + cls}, t("req_" + (v || "unknown")));
  }

  function evidenceTable(items) {
    return table([t("col_requirement"), t("col_result"), t("col_evidence"), t("hr_col_checks")], items.map(function (i) {
      return [
        [C.mono(i.requirement_id || "", "small"), el("div", {}, String(i.requirement || ""))],
        verdictChip(i.verdict),
        i.quote ? el("q", {}, String(i.quote)) : el("span", {class: "muted"}, t("no_evidence")),
        (i.flags || []).length ? el("span", {class: "ev-tags"}, i.flags.map(function (f) {
          return el("span", {class: "pill"}, translated("hr_flag_", f));
        })) : el("span", {class: "muted"}, "—")
      ];
    }));
  }

  function decisionForm(run, data, slot) {
    var areaId = U.uid("hr-reason");
    var area = el("textarea", {id: areaId, class: "input", rows: "3", maxlength: "2000", required: true,
                               placeholder: t("hr_reason_placeholder")});
    var msg = el("div", {class: "small", role: "status", "aria-live": "polite"});
    var buttons = DECISIONS.map(function (d) {
      var b = el("button", {class: d === "reject" ? "btn btn-danger-outline" : (d === "advance" ? "btn btn-primary" : "btn btn-outline"),
                            type: "button", disabled: true}, t("hr_decision_" + d));
      b.addEventListener("click", function () { submit(d); });
      return b;
    });
    function sync() {
      var ok = !!area.value.trim();
      buttons.forEach(function (b) { b.disabled = !ok; });
    }
    area.addEventListener("input", sync);
    function submit(decision) {
      var reason = area.value.trim();
      if (!reason) {  // a decision without a reason is refused here and by the server
        msg.textContent = t("hr_reason_required");
        area.focus();
        return;
      }
      buttons.forEach(function (b) { b.disabled = true; });
      msg.textContent = t("running");
      KB.api.post("/api/hr/screenings/decision", {thread_id: run.thread_id, candidate_id: data.candidate_id,
                                                  decision: decision, reason: reason})
        .then(function (r) {
          var s = st("screen");
          s.run = r.run || s.run;
          s.decided = {decision: r.decision, screening_id: r.screening_id};
          U.toast(t("hr_decided_toast"), "ok");
          U.clear(slot).appendChild(renderScreening(s.run, slot));
        })
        .catch(function (e) {
          if (e.status === 401) return KB.app.sessionLost();
          msg.textContent = "";
          msg.appendChild(C.errorBox(e));
          sync();
        });
    }
    return el("div", {class: "decide"}, [
      el("h3", {class: "label"}, t("hr_decide_title")),
      el("p", {class: "small"}, [C.icon("shield", "icon-xs"), t("hr_decision_note")]),
      el("label", {for: areaId, class: "label"}, t("hr_reason_label")),
      area,
      el("div", {class: "decide-row"}, buttons),
      msg
    ]);
  }

  function renderScreening(run, slot) {
    var data = (run.output && run.output.data) || {};
    var body = [runHead(run)];
    if (run.status === "error" && run.error) {
      body.push(C.errorBox({code: run.error.code, serverMessage: run.error.message}));
      body.push(C.timeline(run.events, false));
      return el("article", {class: "card run-card run-error"}, [el("div", {class: "run-body"}, body)]);
    }
    if (data.kind !== "hr_screening") return C.runCard(run, {openTimeline: true});  // older recordings
    if (data.refused) {
      body.push(C.panel("info", "info", t("hr_refused_" + data.refused)));
    } else {
      body.push(el("p", {class: "small"}, [t("hr_candidate"), " ", C.mono(data.candidate_id || "?"), " · ",
        t("hr_requisition"), " ", C.mono(data.req_id || "?"), " · ", t("hr_model_route"), " ", C.routeBadge(data.model_route),
        " ", C.levelChip(data.level || 4, {compact: true})]));
      if (data.embedded_instruction) {
        body.push(C.panel("wait", "alert", t("hr_injected_title"), [el("p", {}, t("hr_injected_text"))]));
      }
      body.push(el("p", {class: "small muted"}, [C.icon("eyeoff", "icon-xs"),
        t("hr_removed", {list: (data.protected_removed || []).map(function (k) { return translated("hr_kind_", k); }).join(", ") || "—"})]));
      body.push(evidenceTable(data.items || []));
      body.push(el("p", {class: "small"}, [C.icon("info", "icon-xs"), t("data_no_score")]));
      if (data.decision && data.decision.decision) {
        body.push(C.panel("info", "checkcircle", t("hr_decided", {decision: t("hr_decision_" + data.decision.decision)}), [
          el("p", {class: "small"}, [t("hr_screening_id"), " ", C.mono(data.decision.screening_id || "", "small")]),
          el("p", {class: "small"}, t("hr_reason_stored"))
        ]));
      } else if (data.awaiting_decision) {
        body.push(decisionForm(run, data, slot));
      }
      body.push(el("details", {}, [el("summary", {class: "small"}, t("hr_resume_details")),
        el("p", {class: "small muted"}, t("hr_resume_bias_note")), resumeBlock(data.candidate_id, "hr_screening")]));
    }
    body.push(C.timeline(run.events, false));
    return el("article", {class: "card run-card run-" + (run.status || "x")}, [el("div", {class: "run-body"}, body)]);
  }

  function screen(candidateId, slot) {
    U.clear(slot).appendChild(C.loading(t("hr_screening_running")));
    // one conversation per screening: the thread then waits for exactly this decision
    KB.api.post("/api/runs", {app: "hr_screening", input: candidateId, thread_id: null, user_level: 1}).then(function (run) {
      var s = st("screen");
      s.run = run;
      KB.app.rememberRun(run, candidateId);
      U.clear(slot).appendChild(renderScreening(run, slot));
      slot.scrollIntoView({block: "nearest"});
    }, function (e) { failed(slot, e); });
  }

  function candidateTables(data, onPick, app) {
    var out = [];
    var reqs = data.requisitions || [];
    if (!reqs.length) {
      (data.denied || []).forEach(function (d) { out.push(C.denied(deniedReason(d))); });
      return out;
    }
    reqs.forEach(function (r) {
      var head = el("h3", {class: "label"}, [C.mono(r.req_id), " ", String(r.title || ""), " ",
        r.status ? el("span", {class: "pill"}, translated("hr_req_status_", r.status)) : null, " ",
        r.own ? el("span", {class: "pill pill-strong"}, t("hr_own_req")) : null]);
      if (r.denied) {
        out.push(el("div", {class: "card pad"}, [head, C.denied(t("hr_req_denied", {id: r.req_id}))]));
        return;
      }
      out.push(el("div", {class: "card pad"}, [head, table(
        [t("hr_col_candidate"), t("hr_col_name"), t("hr_col_stage"), t("hr_col_applied"), t("hr_col_source"), ""],
        (r.candidates || []).map(function (c) {
          return [C.mono(c.candidate_id), String(c.name || ""), translated("hr_stage_", c.stage), String(c.applied_on || ""),
                  String(c.source || ""), onPick(c)];
        }))]));
    });
    return out;
  }

  KB.views = KB.views || {};
  KB.views.hr = {
    render: function (root) {
      if (!header(root, "hr", "hr_screening")) return;
      var s = st("screen");
      root.appendChild(C.panel("info", "shield", t("hr_ai_act_title"), [el("p", {}, t("hr_ai_act_text")),
        el("p", {class: "small"}, t("app_hr_screening_safety"))]));
      KB.appPage.demoCoverage(root, "hr_screening");  // demo: say up front when this persona has no recording
      var slot = el("div", {class: "result-slot", "aria-live": "polite"});
      var pick = el("div", {class: "result-slot"});
      var inputId = U.uid("hr-cand");
      var input = el("input", {id: inputId, class: "input", type: "text", maxlength: "40", placeholder: "CAND-001"});
      var form = el("form", {class: "card pad form-inline", novalidate: true}, [
        el("label", {for: inputId, class: "label"}, t("hr_candidate_input")),
        el("div", {class: "form-row"}, [input, el("button", {class: "btn btn-primary", type: "submit"},
          [C.icon("usercheck", "icon-sm"), t("hr_screen_btn")])])
      ]);
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var v = input.value.trim();
        if (!v) { input.focus(); return; }
        screen(v, slot);
      });
      root.appendChild(form);
      root.appendChild(el("h2", {class: "section-title"}, t("hr_pick_title")));
      root.appendChild(pick);
      root.appendChild(el("h2", {class: "section-title"}, t("hr_workspace_title")));
      root.appendChild(slot);
      pick.appendChild(C.loading(t("loading")));
      KB.api.get("/api/hr/candidates?app=hr_screening").then(function (data) {
        U.clear(pick);
        candidateTables(data, function (c) {
          var b = el("button", {class: "btn btn-outline btn-sm", type: "button"}, t("hr_screen_btn"));
          b.addEventListener("click", function () { screen(c.candidate_id, slot); });
          // demo: a candidate whose screening has no recording gets a disabled button that says so
          return KB.appPage.markUnrecorded(b, "hr_screening", c.candidate_id);
        }, "hr_screening").forEach(function (n) { pick.appendChild(n); });
      }, function (e) { failed(pick, e); });
      if (s.run) slot.appendChild(renderScreening(s.run, slot));
      else slot.appendChild(el("p", {class: "muted small"}, t("hr_workspace_empty")));
    }
  };

  // ---------------------------------------------------------------- headcount (#/headcount)

  function publicCard(pub, run, data) {
    var reqRows = (pub.requisitions_by_dept || []).map(function (d) {
      return [deptName(d.dept), String(d.open_headcount), el("ul", {class: "steps"}, (d.roles || []).map(function (r) {
        return el("li", {}, [C.mono(r.req_id || "", "small"), " ", String(r.title || ""), " · ",
          translated("hr_req_status_", r.status), " · ", t("hr_heads", {n: r.headcount})]);
      }))];
    });
    var staff = pub.staff_by_dept || [];
    var max = Math.max.apply(null, staff.map(function (s) { return s.employees || 0; }).concat([1]));
    var parts = [
      el("h2", {class: "card-title"}, [t("hr_public_title"), " ", C.levelChip(2, {compact: true})]),
      table([t("hr_col_dept"), t("hr_col_open"), t("hr_col_roles")], reqRows),
      el("h3", {class: "label"}, t("hr_staff_title", {n: pub.min_group || 5}))
    ];
    if (staff.length) {
      parts.push(el("div", {class: "bars"}, staff.map(function (s) {
        var hidden = s.suppressed || s.employees === null || s.employees === undefined;
        return el("div", {class: "bar-row"}, [
          el("span", {class: "bar-label"}, deptName(s.dept)),
          el("span", {class: "bar-track"}, hidden ? null : [el("span", {class: "bar-fill", style: "width:" + Math.round(100 * s.employees / max) + "%"})]),
          el("span", {class: "bar-val"}, hidden ? t("hr_hidden_small") : String(s.employees))
        ]);
      })));
    }
    var denied = (pub.staff_denied || []).map(function (d) { return deptName(d.dept); });
    if (denied.length) parts.push(C.denied(t("hr_staff_denied", {list: denied.join(", ")})));
    return el("div", {class: "card pad"}, parts);
  }

  function budgetCard(b) {
    var parts = [el("h2", {class: "card-title"}, [t("hr_budget_title"), " ", C.levelChip(3, {compact: true})])];
    if (!b || !b.allowed) {
      parts.push(C.denied(t("hr_budget_denied")));
      return el("div", {class: "card pad"}, parts);
    }
    parts.push(table([t("hr_col_req"), t("hr_col_dept"), t("hr_col_approved"), t("hr_col_projected"), t("hr_col_variance"), ""],
      (b.items || []).map(function (i) {
        return [C.mono(i.req_id), deptName(i.dept), num(i.approved_budget_eur), num(i.projected_cost_eur),
                i.variance_pct === null || i.variance_pct === undefined ? "—" : (i.variance_pct > 0 ? "+" : "") + i.variance_pct + " %",
                i.over_budget ? el("span", {class: "pill pill-bad"}, t("hr_over_budget")) : ""];
      })));
    if ((b.denied || []).length) {
      parts.push(C.denied(t("hr_budget_rows_denied", {list: b.denied.map(function (d) { return d.req_id; }).join(", ")})));
    }
    return el("div", {class: "card pad"}, parts);
  }

  function num(v) {
    return typeof v === "number" ? v.toLocaleString(KB.lang.current === "zh" ? "zh-CN" : "en-GB") + " EUR" : "—";
  }

  function renderHeadcount(run) {
    var data = (run.output && run.output.data) || {};
    var body = [runHead(run)];
    if (run.status === "error" && run.error) {
      body.push(C.errorBox({code: run.error.code, serverMessage: run.error.message}));
    } else if (data.kind !== "hr_headcount") {
      return C.runCard(run, {openTimeline: true});  // older recordings
    } else {
      var nr = data.note_route || {};
      body.push(el("div", {class: "card pad"}, [
        el("h3", {class: "label"}, [t("hr_note_title"), " ", C.routeBadge(nr.route), " ", C.levelChip(nr.level || run.hwm, {compact: true})]),
        el("div", {class: "answer"}, String((run.output && run.output.text) || "")),
        data.note_source === "code" ? null : C.fakeModelNote(run),
        el("p", {class: "small muted"}, t(data.note_source === "code" ? "hr_note_code" : "hr_note_hint"))
      ]));
      body.push(el("div", {class: "dept-grid"}, [publicCard(data.public || {}, run, data), budgetCard(data.budget)]));
    }
    body.push(C.timeline(run.events, false));
    return el("article", {class: "card run-card run-" + (run.status || "x")}, [el("div", {class: "run-body"}, body)]);
  }

  KB.views.headcount = {
    render: function (root) {
      if (!header(root, "headcount", "hr_headcount")) return;
      var s = st("headcount");
      root.appendChild(C.panel("info", "shield", t("hr_headcount_rules_title"), [
        el("p", {}, t("app_hr_headcount_step1")), el("p", {class: "small"}, t("hr_headcount_rules_text"))]));
      var slot = el("div", {class: "result-slot", "aria-live": "polite"});
      var btn = el("button", {class: "btn btn-primary", type: "button"}, [C.icon("refresh", "icon-sm"), t("hr_headcount_btn")]);
      btn.addEventListener("click", function () {
        btn.disabled = true;
        U.clear(slot).appendChild(C.loading(t("running")));
        KB.api.post("/api/runs", {app: "hr_headcount", input: t("hr_headcount_question"), thread_id: null, user_level: 1})
          .then(function (run) {
            s.run = run;
            KB.app.rememberRun(run, t("hr_headcount_btn"));
            U.clear(slot).appendChild(renderHeadcount(run));
          }, function (e) { failed(slot, e); })
          .then(function () { btn.disabled = false; });
      });
      root.appendChild(el("div", {class: "card pad row-actions"}, [btn, el("span", {class: "small muted"}, t("hr_headcount_btn_hint"))]));
      root.appendChild(slot);
      if (s.run) slot.appendChild(renderHeadcount(s.run));
    }
  };

  // ---------------------------------------------------------------- candidates (#/candidates)

  KB.views.candidates = {
    render: function (root) {
      if (!header(root, "candidates", "hr_candidates")) return;
      root.appendChild(C.panel("info", "shield", t("hr_cand_notice_title"), [
        el("p", {}, t("app_hr_candidates_step3")), el("p", {class: "small"}, t("app_hr_candidates_safety"))]));
      var list = el("div", {class: "result-slot", "aria-live": "polite"});
      var detail = el("div", {class: "result-slot"});
      root.appendChild(list);
      root.appendChild(detail);
      list.appendChild(C.loading(t("loading")));
      KB.api.get("/api/hr/candidates?app=hr_candidates").then(function (data) {
        U.clear(list);
        candidateTables(data, function (c) {
          var b = el("button", {class: "btn btn-outline btn-sm", type: "button"}, t("hr_show_resume"));
          b.addEventListener("click", function () {
            U.clear(detail).appendChild(el("h2", {class: "section-title"}, [t("hr_candidate"), " ", C.mono(c.candidate_id)]));
            var blk = resumeBlock(c.candidate_id, "hr_candidates");
            detail.appendChild(blk);
            blk.querySelector("button").click();
          });
          return b;
        }, "hr_candidates").forEach(function (n) { list.appendChild(n); });
      }, function (e) { failed(list, e); });
    }
  };
})();
