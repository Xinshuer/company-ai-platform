// Business apps that arrive in later milestones (legal review M2, management report M3,
// sales profile and HR M4). Each page already renders the §18 run shape: a sample run from the
// fixtures, plus a form that posts to /api/runs, so the page works unchanged once the graph lands.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var APPS = {
    legal: {app: "legal_review", milestone: "M2", icon: "scale", steps: 4},
    report: {app: "report", milestone: "M3", icon: "chart", steps: 4},
    sales: {app: "sales_profile", milestone: "M4", icon: "target", steps: 4},
    hr: {app: "hr_screening", milestone: "M4", icon: "usercheck", steps: 4},
    headcount: {app: "hr_headcount", milestone: "M4", icon: "users", steps: 3}
  };

  function hasApp(id) {
    return (KB.state.me.apps || []).some(function (a) { return a.id === id; });
  }

  function render(route, root) {
    var cfg = APPS[route];
    var id = cfg.app;
    root.appendChild(C.pageHeader("h-" + route, "app_" + id, "app_" + id + "_desc",
      [el("span", {class: "pill pill-strong"}, t("milestone_pill", {m: cfg.milestone}))]));

    if (!hasApp(id)) {
      // explicit denial, never a silently empty page (ENGINEERING.md rule 6)
      root.appendChild(C.denied(t("app_denied", {app: t("app_" + id)})));
      root.appendChild(el("p", {class: "small muted"}, t("app_" + id + "_who")));
      return;
    }

    root.appendChild(C.panel("info", "info", t("milestone_title", {m: cfg.milestone}), [
      el("p", {}, t("milestone_text", {m: cfg.milestone}))
    ]));

    var steps = [];
    for (var i = 1; i <= cfg.steps; i++) steps.push(el("li", {}, t("app_" + id + "_step" + i)));
    root.appendChild(el("div", {class: "card pad"}, [
      el("h2", {class: "card-title"}, t("how_it_works")),
      el("ol", {class: "steps"}, steps),
      el("p", {class: "small muted"}, [C.icon("shield", "icon-xs"), t("app_" + id + "_safety")])
    ]));

    // Samples are demo recordings (the live server does not serve them). The input is pre-filled
    // only when 运行 would find a recording for this persona.
    var sample = KB.api.mode === "demo" ? KB.demo.sample(id, KB.demo.persona()) : null;
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
          out.appendChild(el("p", {class: "small muted"}, t("milestone_not_yet", {m: cfg.milestone})));
        }
      }).then(function () { btn.disabled = false; });
    });
    root.appendChild(form);
    root.appendChild(out);

    if (sample && sample.run) {
      root.appendChild(el("h2", {class: "section-title"}, [t("sample_run"), " ",
        el("span", {class: "pill"}, t("sample_pill"))]));
      root.appendChild(el("p", {class: "small muted"}, t("sample_run_hint")));
      root.appendChild(C.runCard(sample.run, {trace: sample.runnable}));
    }
  }

  KB.views = KB.views || {};
  Object.keys(APPS).forEach(function (route) {
    KB.views[route] = {render: function (root) { render(route, root); }};
  });
})();
