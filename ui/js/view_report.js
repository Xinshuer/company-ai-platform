// Management report (M3, app report, route #/report).
// Runs the report as the current persona (POST /api/runs {app: "report"}) and shows what came back:
// the report text with every number as a button that opens its provenance (metric id, SQL hash,
// signed by), the sections with their figures and issues, explicit "no permission" sections, the
// route and level, and the watermarked export (a download in live mode; demo shows the watermark).
// Numbers reach the page only as the metric layer rendered them (display_zh / display_en); the
// model's prose is text nodes only. "[no permission or not published]" and "[not shown: group too
// small]" are notices, styled as such, never errors and never zeros.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  // the metric layer's placeholder grammar (corpai/metrics/numbers.py PLACEHOLDER_RE)
  var PLACEHOLDER = /\{metric:([A-Za-z0-9_.@=&\-]+)(?::(\d{4}-(?:\d{2}|Q[1-4])))?\}/g;
  // in the UI language: the report is written in the language of its input (agents/report/request.py)
  function defaultInput() { return t("report_default_input"); }

  function lookup(table, key, period) {
    if (period && table[key + ":" + period]) return table[key + ":" + period];
    return table[key] || null;
  }

  function display(entry, lang) {
    if (!entry) return "?";
    return lang === "en" ? entry.display_en : entry.display_zh;
  }

  // A number as a button: shown exactly as code rendered it, opening its provenance.
  function numButton(entry, lang, prov) {
    var cls = "chip chip-btn";
    if (entry && entry.status === "unavailable") cls = "chip status status-bad";
    else if (entry && entry.suppressed) cls = "chip status status-muted";
    var b = el("button", {class: cls, type: "button", title: t("report_num_hint")}, display(entry, lang));
    b.addEventListener("click", function () { prov.show(entry); });
    return b;
  }

  function inline(line, table, lang, prov) {
    var out = [];
    var last = 0;
    var m;
    PLACEHOLDER.lastIndex = 0;
    while ((m = PLACEHOLDER.exec(line)) !== null) {
      if (m.index > last) out.push(line.slice(last, m.index));
      out.push(numButton(lookup(table, m[1], m[2]), lang, prov));
      last = PLACEHOLDER.lastIndex;
    }
    if (last < line.length) out.push(line.slice(last));
    return out;
  }

  // The report template as headings, notes, bullets and paragraphs. Line-based on purpose: no
  // markdown library, no HTML; every piece of text becomes a text node.
  function reportBody(template, table, lang, prov) {
    var parts = [];
    var list = null;
    (template || "").split("\n").forEach(function (raw) {
      var line = raw.replace(/\s+$/, "");
      if (/^- /.test(line)) {
        if (!list) { list = el("ul", {class: "steps"}); parts.push(list); }
        list.appendChild(el("li", {}, inline(line.slice(2), table, lang, prov)));
        return;
      }
      list = null;
      if (!line) return;
      if (/^# /.test(line)) parts.push(el("h2", {class: "card-title"}, inline(line.slice(2), table, lang, prov)));
      else if (/^## /.test(line)) parts.push(el("h3", {class: "label"}, inline(line.slice(3), table, lang, prov)));
      else if (/^> /.test(line)) parts.push(el("p", {class: "small muted"}, inline(line.slice(2), table, lang, prov)));
      else parts.push(el("p", {}, inline(line, table, lang, prov)));
    });
    return el("div", {class: "answer"}, parts);
  }

  // The provenance panel: one place on the page that answers "where does this number come from".
  function provenancePanel() {
    var body = el("div", {class: "panel-text", "aria-live": "polite"}, [el("p", {class: "muted small"}, t("report_prov_empty"))]);
    var box = el("div", {class: "card pad"}, [el("h3", {class: "label"}, [C.icon("search", "icon-xs"), " ", t("report_prov_title")]), body]);
    function row(k, v) {
      if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return null;
      return [el("dt", {}, t(k)), el("dd", {}, Array.isArray(v) ? v.join(", ") : String(v))];
    }
    return {
      node: box,
      show: function (e) {
        U.clear(body);
        if (!e) return;
        var p = e.provenance || {};
        var name = KB.lang.pick(e, "name") || e.metric_id;
        var dims = Object.keys(e.dims || {}).map(function (k) { return k + "=" + e.dims[k]; }).join(", ");
        body.appendChild(el("p", {}, [el("strong", {}, name), dims ? " (" + dims + ") " : " ",
          el("span", {class: "pill"}, display(e, KB.lang.current))]));
        body.appendChild(el("dl", {class: "kv"}, [
          row("report_prov_metric", e.metric_id), row("report_prov_period", e.period),
          row("report_prov_status", KB.lang.has("mstatus_" + e.status) ? t("mstatus_" + e.status) : e.status),
          row("report_prov_sql", p.sql_sha256), row("report_prov_formula", p.formula),
          row("report_prov_sources", p.sources), row("report_prov_signed", p.signed_by),
          row("report_prov_signed_at", p.signed_at ? U.fmtTime(p.signed_at) : null),
          row("report_prov_published", p.published_at ? U.fmtTime(p.published_at) : null),
          row("report_prov_version", p.version), row("report_prov_signal", p.signal ? label("sig_", p.signal) + " · " + label("sigf_", p.field) : null),
          row("report_prov_reason", e.reason)
        ]));
        body.appendChild(el("p", {class: "small"}, [C.levelChip(e.level, {compact: true})]));
        box.scrollIntoView && box.scrollIntoView({block: "nearest"});
      }
    };
  }

  function statusChip(st) {
    var cls = st === "ok" ? "ok" : (st === "partial" ? "wait" : "bad");
    return el("span", {class: "chip status status-" + cls}, t("report_section_" + st));
  }

  function label(prefix, code) {
    return KB.lang.has(prefix + code) ? t(prefix + code) : String(code);
  }

  // The detector's evidence as key: value pairs. A figure with a table entry (signal.<kind>.<ref>.<field>)
  // is shown as code rendered it, with its provenance; ids, dates and other values as they are, with the
  // field names, flags and departments in the page's language (issues-log WR-14).
  function evidenceValue(k, v) {
    if (Array.isArray(v)) return v.join(", ");
    if (typeof v === "boolean") return t(v ? "report_bool_true" : "report_bool_false");
    if (k === "dept") return label("dept_", v);
    if (k === "contract_type") return label("legal_ctype_", String(v).toLowerCase());
    return String(v);
  }

  function evidence(kind, f, table, lang, prov) {
    return el("dl", {class: "kv kv-inline small"}, Object.keys(f).filter(function (k) { return k !== "ref"; }).reduce(function (acc, k) {
      var v = f[k];
      if (v === null || v === undefined || (typeof v === "object" && !Array.isArray(v))) return acc;
      var entry = table["signal." + kind + "." + f.ref + "." + k];
      acc.push(el("dt", {title: k}, label("sigf_", k)));
      acc.push(el("dd", {}, entry ? numButton(entry, lang, prov) : evidenceValue(k, v)));
      return acc;
    }, []));
  }

  function signalsBlock(sec, table, lang, prov) {
    if (!(sec.signals || []).length) return null;
    return el("div", {}, [el("h4", {class: "label"}, t("report_signals")), el("ul", {class: "steps"}, sec.signals.map(function (s) {
      var title = label("sig_", s.kind);
      if (s.status !== "ok") {
        return el("li", {}, [el("strong", {}, title), " ", el("span", {class: "chip status status-bad"},
          [C.icon("lock", "icon-xs"), t("report_signal_denied", {sources: (s.denied_sources || []).join(", ") || "—"})])]);
      }
      if (!(s.findings || []).length) {
        return el("li", {}, [el("strong", {}, title), " ", el("span", {class: "muted"}, t("report_signal_none"))]);
      }
      return el("li", {}, [el("strong", {}, title), " ", el("span", {class: "pill pill-bad"}, t("report_signal_found", {n: s.findings.length})),
        " ", C.levelChip(s.level, {compact: true}),
        el("ul", {}, s.findings.map(function (f) { return el("li", {}, [C.mono(f.ref), evidence(s.kind, f, table, lang, prov)]); }))]);
    }))]);
  }

  function sectionCard(sec, table, lang, prov) {
    var title = KB.lang.pick(sec, "title");
    var head = el("div", {class: "prop-head"}, [el("h3", {class: "card-title"}, title), statusChip(sec.status),
      sec.status === "no_permission" ? null : C.levelChip(sec.level, {compact: true})]);
    if (sec.status === "no_permission") {
      return el("article", {class: "card pad"}, [head, C.panel("bad", "lock", t("report_section_no_permission"),
        [el("p", {}, t("report_section_denied"))]), signalsBlock(sec, table, lang, prov)]);
    }
    var rows = (sec.metrics || []).map(function (m) {
      var e = table[m.key] || m;
      var dims = Object.keys(m.dims || {}).map(function (k) { return k + "=" + m.dims[k]; }).join(", ");
      return el("tr", {class: m.status === "unavailable" ? "row-deny" : null}, [
        el("td", {}, [KB.lang.pick(e, "name") || m.metric_id, dims ? el("span", {class: "muted small"}, " " + dims) : null]),
        el("td", {}, numButton(e, lang, prov)),
        el("td", {}, el("span", {class: "chip status status-" + (m.status === "unavailable" ? "bad" : "ok")},
          KB.lang.has("mstatus_" + m.status) ? t("mstatus_" + m.status) : m.status)),
        el("td", {}, m.status === "unavailable" ? el("span", {class: "muted"}, "—") : C.levelChip(m.level, {compact: true}))
      ]);
    });
    return el("article", {class: "card pad"}, [head,
      el("div", {class: "table-wrap"}, el("table", {class: "table"}, [
        el("thead", {}, el("tr", {}, [t("report_col_metric"), t("report_col_value"), t("report_col_status"), t("report_col_level")]
          .map(function (h) { return el("th", {scope: "col"}, h); }))),
        el("tbody", {}, rows)])),
      signalsBlock(sec, table, lang, prov)]);
  }

  function saveFile(name, text) {
    var blob = new Blob([text], {type: "text/markdown;charset=utf-8"});
    var url = URL.createObjectURL(blob);
    var a = el("a", {href: url, download: name, class: "sr-only"}, name);
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.parentNode && a.parentNode.removeChild(a); }, 1000);
  }

  function exportBar(run, data) {
    var slot = el("div", {class: "result-slot", "aria-live": "polite"});
    var allowed = data.export && data.export.allowed;
    var btn = el("button", {class: "btn btn-outline btn-sm", type: "button", disabled: !allowed},
      [C.icon("doc", "icon-sm"), t("report_export")]);
    btn.addEventListener("click", function () {
      btn.disabled = true;
      KB.api.post("/api/reports/" + encodeURIComponent(run.run_id) + "/export", {thread_id: run.thread_id}).then(function (f) {
        U.clear(slot);
        var lines = String(f.content || "").split("\n");
        if (KB.api.mode === "live") {
          saveFile(f.filename, f.content);
          slot.appendChild(C.panel("info", "check", t("report_export_done", {file: f.filename}),
            [el("p", {class: "small"}, t("report_export_live")), el("pre", {class: "json"}, lines[0] || "")]));
        } else {
          slot.appendChild(C.panel("info", "info", t("report_export_done", {file: f.filename}),
            [el("p", {class: "small"}, t("report_export_demo")), el("pre", {class: "json", tabindex: "0"}, f.content)]));
        }
        U.toast(t("report_export_done", {file: f.filename}), "ok");
      }).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        U.clear(slot).appendChild(C.errorBox(e));
      }).then(function () { btn.disabled = !allowed; });
    });
    return el("div", {class: "card pad"}, [
      el("div", {class: "row-actions"}, [btn, allowed ? null : el("span", {class: "small muted"}, t("report_export_denied"))]),
      el("p", {class: "small muted"}, [C.icon("shield", "icon-xs"), t("report_export_hint")]),
      slot]);
  }

  function renderRun(out, run) {
    U.clear(out);
    var head = el("div", {class: "run-head"}, [C.statusChip(run.status), C.routeBadge(run.route), C.hwmChip(run.hwm),
      el("span", {class: "spacer"}),
      C.canTrace(run) ? el("a", {class: "link-btn", href: "#/trace/" + encodeURIComponent(run.run_id)},
        [C.icon("activity", "icon-xs"), t("view_trace")]) : null]);
    out.appendChild(head);
    if (run.status === "error" && run.error) {
      out.appendChild(C.errorBox({code: run.error.code, serverMessage: run.error.message}));
      out.appendChild(C.timeline(run.events, true));
      return;
    }
    var data = (run.output && run.output.data) || {};
    if (data.kind !== "management_report") {
      U.clear(out).appendChild(C.runCard(run, {openTimeline: false}));  // not a report payload: the generic card
      return;
    }
    if (data.refused || !data.template) {
      out.appendChild(C.panel("info", "info", t("report_refused"), [el("p", {}, (run.output && run.output.text) || "")]));
      out.appendChild(C.timeline(run.events, false));
      return;
    }
    var lang = data.lang === "en" ? "en" : "zh";
    var table = data.table || {};
    var prov = provenancePanel();
    out.appendChild(el("p", {class: "small muted"}, [t("report_period_line", {period: data.period, as_of: data.as_of}), " · ",
      t("report_level") + " ", C.levelChip(data.level, {compact: true})]));
    if ((run.hwm || 0) >= 3) out.appendChild(el("p", {class: "small"}, [C.icon("cpu", "icon-xs"), t("report_local_note", {level: run.hwm})]));
    if (data.used_fallback) out.appendChild(C.panel("wait", "alert", t("report_fallback"), null));
    out.appendChild(exportBar(run, data));
    out.appendChild(el("article", {class: "card pad"}, [
      el("p", {class: "small muted"}, [C.icon("info", "icon-xs"), t("report_num_hint")]),
      C.fakeModelNote({route: run.route, output: {text: data.template}}),
      reportBody(data.template, table, lang, prov)]));
    out.appendChild(prov.node);
    out.appendChild(el("h2", {class: "section-title"}, t("report_sections")));
    (data.sections || []).forEach(function (sec) { out.appendChild(sectionCard(sec, table, lang, prov)); });
    out.appendChild(C.timeline(run.events, false));
    out.appendChild(el("p", {class: "run-id small muted"}, [t("run_id") + " ", C.mono(run.run_id || "—", "small"),
      run.thread_id ? [" · " + t("thread_id") + " ", C.mono(run.thread_id, "small")] : null]));
  }

  // demo: recordings are matched by period, so the localised default still finds a 2026-Q3 recording
  function recordedDefault(recorded) {
    if (!recorded.length || recorded.some(function (s) { return s.indexOf("2026-Q3") >= 0; })) return defaultInput();
    return recorded[0];
  }

  function form(root, out) {
    var key = KB.demo && KB.api.mode === "demo" ? KB.demo.persona() : null;
    // recordings that name a period: the report reads only the period from the input
    var recorded = (key ? KB.demo.suggestions(key, "report") : []).filter(function (s) {
      return /\d{4}-(Q[1-4]|\d{2})/.test(s);
    });
    var inputId = U.uid("report-input");
    var input = el("input", {id: inputId, class: "input", type: "text", maxlength: "200", value: recordedDefault(recorded)});
    var btn = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("send", "icon-sm"), t("report_run")]);
    var f = el("form", {class: "card pad form-inline", novalidate: true}, [
      el("label", {for: inputId, class: "label"}, t("app_report_input")),
      el("div", {class: "form-row"}, [input, btn]),
      el("p", {class: "small muted"}, t("report_input_hint")),
      recorded.length ? el("p", {class: "small"}, [t("report_recorded") + " "].concat(recorded.map(function (s) {
        var b = el("button", {class: "chip chip-btn", type: "button"}, s);
        b.addEventListener("click", function () { input.value = s; });
        return b;
      }))) : null
    ]);
    f.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var text = input.value.trim() || defaultInput();
      btn.disabled = true;
      U.clear(out).appendChild(C.loading(t("running")));
      KB.api.post("/api/runs", {app: "report", input: text, thread_id: null, user_level: 1}).then(function (run) {
        KB.app.rememberRun(run, text);
        renderRun(out, run);
      }).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        U.clear(out).appendChild(C.errorBox(e));
      }).then(function () { btn.disabled = false; });
    });
    root.appendChild(f);
  }

  KB.views = KB.views || {};
  KB.views.report = {
    render: function (root, args) {
      root.appendChild(C.pageHeader("h-report", "app_report", "app_report_desc"));
      if (!KB.appPage.hasApp("report")) {
        KB.appPage.denied(root, "report");  // explicit denial; the server answers 403 as well
        return;
      }
      var how = el("details", {class: "card pad"}, [el("summary", {}, t("how_it_works")),
        el("ol", {class: "steps"}, [1, 2, 3, 4].map(function (i) { return el("li", {}, t("app_report_step" + i)); })),
        el("p", {class: "small muted"}, [C.icon("shield", "icon-xs"), t("app_report_safety")])]);
      root.appendChild(how);
      KB.appPage.demoCoverage(root, "report");  // demo: say up front when this persona has no recording
      var out = el("div", {class: "result-slot", "aria-live": "polite"});
      form(root, out);
      root.appendChild(out);
    },
    // for tests and the trace page: draw a recorded run without the form
    renderRun: renderRun
  };
})();
