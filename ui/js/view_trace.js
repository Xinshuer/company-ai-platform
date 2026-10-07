// Trace viewer: the span tree of one run (own runs only; anything else is a 404).
// Text is shown exactly as the server returns it, already masked by level at span end;
// L4 spans never carry text and show "仅元数据 / metadata only".
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var KIND_ICON = {node: "node", llm: "sparkle", tool: "tool", retrieval: "database", policy: "shieldcheck"};

  function buildTree(spans) {
    var byId = {};
    var roots = [];
    spans.forEach(function (s) { byId[s.span_id] = {span: s, children: []}; });
    spans.forEach(function (s) {
      var n = byId[s.span_id];
      if (s.parent_id && byId[s.parent_id]) byId[s.parent_id].children.push(n);
      else roots.push(n);
    });
    function sortRec(list) {
      list.sort(function (a, b) { return String(a.span.started_at).localeCompare(String(b.span.started_at)); });
      list.forEach(function (n) { sortRec(n.children); });
    }
    sortRec(roots);
    return roots;
  }

  function textBlock(label, text, level) {
    var lvl = parseInt(level, 10) || 4;
    var body;
    if (lvl >= 4) body = el("p", {class: "meta-only"}, [C.icon("eyeoff", "icon-xs"), t("metadata_only")]);
    else if (text === null || text === undefined || text === "") body = el("p", {class: "muted small"}, t("no_text"));
    else body = el("pre", {class: "span-text"}, String(text));
    return el("div", {class: "span-block"}, [el("span", {class: "label"}, label), body]);
  }

  function spanRow(node, depth, t0, total) {
    var s = node.span;
    var dur = U.ms(s.started_at, s.ended_at);
    var start = U.ms(t0, s.started_at) || 0;
    var left = total ? (100 * start / total) : 0;
    var width = total && dur !== null ? Math.max(0.8, 100 * dur / total) : 0.8;
    var attrs = s.attrs || {};
    var d = el("details", {class: "span kind-" + s.kind + (s.status === "error" ? " span-error" : ""), style: "--depth:" + depth});
    d.appendChild(el("summary", {}, [
      el("span", {class: "span-name"}, [
        el("span", {class: "span-kind", title: t("kind_" + s.kind)}, [C.icon(KIND_ICON[s.kind] || "info", "icon-xs")]),
        el("span", {class: "mono"}, s.name)
      ]),
      el("span", {class: "span-tags"}, [
        C.levelChip(s.level, {compact: true}),
        attrs.route ? C.routeBadge(attrs.route) : null,
        attrs.decision ? C.decisionChip(attrs.decision) : null,
        s.status !== "ok" ? C.statusChip(s.status) : null
      ]),
      el("span", {class: "span-bar", "aria-hidden": "true"}, [
        el("span", {class: "span-bar-fill", style: "left:" + left.toFixed(2) + "%;width:" + Math.min(100 - left, width).toFixed(2) + "%"})
      ]),
      el("span", {class: "span-dur mono"}, U.fmtMs(dur))
    ]));
    d.appendChild(el("div", {class: "span-detail"}, [
      el("dl", {class: "kv kv-inline"}, [
        el("dt", {}, t("col_kind")), el("dd", {}, t("kind_" + s.kind)),
        el("dt", {}, t("col_started")), el("dd", {}, U.fmtTime(s.started_at)),
        el("dt", {}, t("col_span_id")), el("dd", {}, C.mono(s.span_id, "small"))
      ]),
      Object.keys(attrs).length ? el("div", {class: "span-block"}, [el("span", {class: "label"}, t("attrs")), C.jsonBlock(attrs)]) : null,
      textBlock(t("input_text"), s.input_text, s.level),
      textBlock(t("output_text"), s.output_text, s.level)
    ]));
    var frag = [el("li", {class: "span-item"}, [d])];
    node.children.forEach(function (c) { frag = frag.concat(spanRow(c, depth + 1, t0, total)); });
    return frag;
  }

  function renderTrace(slot, tr) {
    U.clear(slot);
    var spans = (tr && tr.spans) || [];
    if (!spans.length) {
      slot.appendChild(el("div", {class: "empty-state"}, [C.icon("activity", "icon-lg"), el("p", {}, t("trace_empty"))]));
      return;
    }
    var t0 = spans.reduce(function (m, s) { return !m || s.started_at < m ? s.started_at : m; }, null);
    var t1 = spans.reduce(function (m, s) { return !m || (s.ended_at || "") > m ? s.ended_at : m; }, null);
    var total = U.ms(t0, t1) || 1;
    var maxLvl = spans.reduce(function (m, s) { return Math.max(m, parseInt(s.level, 10) || 4); }, 1);
    var routes = {};
    spans.forEach(function (s) { if (s.attrs && s.attrs.route) routes[s.attrs.route] = true; });
    slot.appendChild(el("div", {class: "trace-summary card"}, [
      el("div", {class: "stat"}, [el("span", {class: "stat-label"}, t("trace_spans")), el("span", {class: "stat-value"}, String(spans.length))]),
      el("div", {class: "stat"}, [el("span", {class: "stat-label"}, t("trace_duration")), el("span", {class: "stat-value"}, U.fmtMs(total))]),
      el("div", {class: "stat"}, [el("span", {class: "stat-label"}, t("trace_max_level")), el("span", {class: "stat-value"}, [C.levelChip(maxLvl)])]),
      el("div", {class: "stat"}, [el("span", {class: "stat-label"}, t("trace_routes")), el("span", {class: "stat-value"},
        Object.keys(routes).length ? Object.keys(routes).map(C.routeBadge) : [el("span", {class: "muted"}, "—")])])
    ]));
    slot.appendChild(el("p", {class: "small muted"}, t("trace_mask_note")));
    var tree = buildTree(spans);
    var rows = [];
    tree.forEach(function (n) { rows = rows.concat(spanRow(n, 0, t0, total)); });
    slot.appendChild(el("ol", {class: "span-tree card", "aria-label": t("trace_tree")}, rows));
  }

  KB.views = KB.views || {};
  KB.views.trace = {
    render: function (root, args) {
      var runId = args && args[0] ? decodeURIComponent(args[0]) : "";
      root.appendChild(C.pageHeader("h-trace", "nav_trace", "trace_sub"));
      var inputId = U.uid("trace-input");
      var input = el("input", {id: inputId, class: "input mono", type: "text", value: runId, placeholder: "run_…", spellcheck: "false"});
      var form = el("form", {class: "card pad form-inline", novalidate: true}, [
        el("label", {for: inputId, class: "label"}, t("trace_run_id")),
        el("div", {class: "form-row"}, [input, el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("search", "icon-sm"), t("trace_open")])])
      ]);
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var v = input.value.trim();
        if (v) window.location.hash = "#/trace/" + encodeURIComponent(v);
      });
      root.appendChild(form);

      var recent = KB.state.recentRuns || [];
      if (recent.length) {
        root.appendChild(el("div", {class: "recent"}, [el("span", {class: "small muted"}, t("trace_recent")),
          recent.slice(0, 6).map(function (r) {
            return el("a", {class: "chip-btn", href: "#/trace/" + encodeURIComponent(r.run_id), title: r.run_id},
              [C.routeBadge(r.route), " ", r.label || r.run_id]);
          })]));
      }

      var slot = el("div", {class: "trace-slot", "aria-live": "polite"});
      root.appendChild(slot);
      if (!runId) {
        slot.appendChild(el("p", {class: "muted"}, t("trace_pick")));
        return;
      }
      slot.appendChild(C.loading());
      KB.api.get("/api/runs/" + encodeURIComponent(runId) + "/trace").then(function (tr) {
        renderTrace(slot, tr);
      }).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        U.clear(slot).appendChild(C.errorBox(e));
        if (e.status === 404) slot.appendChild(el("p", {class: "small muted"}, t("trace_404_hint")));
      });
    }
  };
})();
