// Shared building blocks for the business app pages (KB.appPage). The pages themselves live in
// view_legal.js (M2), view_report.js and view_metrics.js (M3), view_sales.js and view_hr.js (M4),
// each owned by its milestone. Until a milestone replaces its page, it renders the placeholder
// below: what the app will do, the §18 run form (POST /api/runs) and a recorded sample, so the page
// keeps working once the graph lands.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var P = (KB.appPage = {});

  P.hasApp = function (id) {
    var me = KB.state.me;
    return !!me && (me.apps || []).some(function (a) { return a.id === id; });
  };

  // Explicit denial for a persona without the app, never a silently empty page (ENGINEERING.md rule 6).
  P.denied = function (root, id) {
    root.appendChild(C.denied(t("app_denied", {app: t("app_" + id)})));
    root.appendChild(el("p", {class: "small muted"}, t("app_" + id + "_who")));
  };

  // "Arrives in Mx" panel plus the numbered steps under "how it works".
  // cfg: {milestone, stepKey: "app_legal_review_step" | "metrics_step", steps, safetyKey, textKey?}
  P.comingSoon = function (root, cfg) {
    root.appendChild(C.panel("info", "info", t("milestone_title", {m: cfg.milestone}), [
      el("p", {}, t(cfg.textKey || "milestone_text", {m: cfg.milestone}))
    ]));
    var steps = [];
    for (var i = 1; i <= cfg.steps; i++) steps.push(el("li", {}, t(cfg.stepKey + i)));
    root.appendChild(el("div", {class: "card pad"}, [
      el("h2", {class: "card-title"}, t("how_it_works")),
      el("ol", {class: "steps"}, steps),
      el("p", {class: "small muted"}, [C.icon("shield", "icon-xs"), t(cfg.safetyKey)])
    ]));
  };

  // A form that posts {app, input} to /api/runs and renders the §18 run.
  P.runForm = function (root, id, milestone, opts) {
    var sample = opts && opts.sample;
    var inputId = U.uid("app-input");
    var input = el("input", {id: inputId, class: "input", type: "text", maxlength: "500",
                             value: sample && sample.runnable ? sample.input : ""});
    var btn = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("send", "icon-sm"), t("run_app")]);
    var out = el("div", {class: "result-slot", "aria-live": "polite"});
    var form = el("form", {class: "card pad form-inline", novalidate: true}, [
      el("label", {for: inputId, class: "label"}, t("app_" + id + "_input")),
      el("div", {class: "form-row"}, [input, btn])
    ]);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var text = input.value.trim();
      if (!text) { input.focus(); return; }
      btn.disabled = true;
      U.clear(out).appendChild(C.loading(t("running")));
      KB.api.post("/api/runs", {app: id, input: text, thread_id: null, user_level: 1}).then(function (run) {
        KB.app.rememberRun(run, text);
        U.clear(out).appendChild(C.runCard(run, {openTimeline: true}));
      }).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        U.clear(out).appendChild(C.errorBox(e));
        if (KB.api.mode === "live" && (e.status === 404 || e.status === 400 || e.status === 501)) {
          out.appendChild(el("p", {class: "small muted"}, t("milestone_not_yet", {m: milestone})));
        }
      }).then(function () { btn.disabled = false; });
    });
    root.appendChild(form);
    root.appendChild(out);
    return {form: form, input: input, out: out};
  };

  // Samples are demo recordings (the live server does not serve them). The input is pre-filled
  // only when 运行 would find a recording for this persona.
  P.sample = function (id) {
    return KB.api.mode === "demo" ? KB.demo.sample(id, KB.demo.persona()) : null;
  };

  // Demo mode: a persona with no recording of this app is told so up front, with the personas who
  // have one, instead of finding out from every button ("no recording"). Live mode: nothing.
  P.demoCoverage = function (root, id) {
    if (KB.api.mode !== "demo" || !KB.demo.recordedPersonas) return null;
    var who = KB.demo.recordedPersonas(id);
    var me = KB.demo.persona();
    if (who === null || who.indexOf(me) >= 0) return null;
    var names = who.map(function (k) { return KB.demo.personaName(k); }).join(", ");
    var note = C.panel("wait", "info", t("demo_no_recording_title"),
      [el("p", {}, who.length ? t("demo_no_recording_text", {list: names}) : t("demo_no_recording_none"))]);
    root.appendChild(note);
    return note;
  };

  // Demo mode: whether running `input` would find a recording for this persona (always true live).
  P.recorded = function (id, input, threadId) {
    return KB.api.mode !== "demo" || !KB.demo.hasRecording || KB.demo.hasRecording(id, input, threadId);
  };

  // Demo mode: a control that could only answer "no recording" is disabled and says why.
  P.markUnrecorded = function (control, id, input, threadId) {
    if (P.recorded(id, input, threadId)) return control;
    control.disabled = true;
    control.setAttribute("disabled", "");
    control.setAttribute("title", t("demo_no_recording_hint"));
    control.setAttribute("data-unrecorded", "1");
    return control;
  };

  P.sampleRun = function (root, sample) {
    if (!sample || !sample.run) return;
    root.appendChild(el("h2", {class: "section-title"}, [t("sample_run"), " ",
      el("span", {class: "pill"}, t("sample_pill"))]));
    root.appendChild(el("p", {class: "small muted"}, t("sample_run_hint")));
    root.appendChild(C.runCard(sample.run, {trace: sample.runnable}));
  };

  // The whole placeholder page of an app. cfg: {route, app, milestone, steps}
  P.render = function (root, cfg) {
    var id = cfg.app;
    root.appendChild(C.pageHeader("h-" + cfg.route, "app_" + id, "app_" + id + "_desc",
      [el("span", {class: "pill pill-strong"}, t("milestone_pill", {m: cfg.milestone}))]));
    if (!P.hasApp(id)) {
      P.denied(root, id);
      return;
    }
    P.comingSoon(root, {milestone: cfg.milestone, stepKey: "app_" + id + "_step", steps: cfg.steps,
                        safetyKey: "app_" + id + "_safety"});
    var sample = P.sample(id);
    P.runForm(root, id, cfg.milestone, {sample: sample});
    P.sampleRun(root, sample);
  };
})();
