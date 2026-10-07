// Metrics and the CFO's sign-off board (M3, route #/metrics; not an app: GET /api/metrics lists what the
// viewer may see). draft → signed → published:
// - the head of finance selects metrics and signs off drafts or publishes signed ones (the API and the
//   database both check; every attempt is audited), and in the demo may recompute any ended period,
//   also one with no snapshot yet (the refresh card sits outside the board for that reason);
// - finance staff and the CEO see drafts and signed rows read-only;
// - everyone else sees only what was published to them; platform roles get an explicit denial.
// What a viewer sees is the server's answer (row-level security), never a filter in this page.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var ORDER = ["draft", "signed", "published"];

  function mstatus(st) {
    var cls = st === "published" ? "ok" : (st === "signed" ? "wait" : (st === "draft" ? "muted" : "bad"));
    return el("span", {class: "chip status status-" + cls}, KB.lang.has("mstatus_" + st) ? t("mstatus_" + st) : st);
  }

  function value(row) {
    if (!row) return el("span", {class: "muted"}, "—");
    var cls = row.status === "unavailable" ? "status status-bad" : (row.suppressed ? "status status-muted" : "pill");
    return el("span", {class: "chip " + cls}, KB.lang.current === "en" ? row.display_en : row.display_zh);
  }

  function roleNote(can) {
    if (can.sign) return C.panel("info", "shieldcheck", t("metrics_role_cfo"), null);
    if (can.drafts) return C.panel("muted", "eyeoff", t("metrics_role_drafts"), null);
    return C.panel("muted", "lock", t("metrics_role_published"), null);
  }

  function whoCard() {
    var flow = el("p", {}, ORDER.map(function (st, i) { return [i ? " → " : "", mstatus(st)]; }));
    return el("div", {class: "card pad"}, [
      el("h2", {class: "card-title"}, t("metrics_who")), flow,
      el("ul", {class: "steps"}, [el("li", {}, t("metrics_who_cfo")), el("li", {}, t("metrics_who_fin")),
        el("li", {}, t("metrics_who_rest"))]),
      el("details", {}, [el("summary", {}, t("how_it_works")),
        el("ol", {class: "steps"}, [1, 2, 3, 4].map(function (i) { return el("li", {}, t("metrics_step" + i)); }))]),
      el("p", {class: "small muted"}, [C.icon("shield", "icon-xs"), t("metrics_safety")])
    ]);
  }

  function board(slot, period, state) {
    U.clear(slot).appendChild(C.loading());
    KB.api.get("/api/metrics/board?period=" + encodeURIComponent(period)).then(function (b) {
      U.clear(slot);
      var can = b.can || {};
      var picked = {};
      var rows = (b.metrics || []).map(function (m) {
        var total = (m.rows || [])[0] || null;
        var box = can.sign ? el("input", {type: "checkbox", "aria-label": t("metrics_select") + " " + m.metric_id}) : null;
        if (box) box.addEventListener("change", function () { picked[m.metric_id] = box.checked; });
        var extra = (m.rows || []).slice(1);
        var detail = extra.length ? el("details", {}, [el("summary", {class: "small"}, t("metrics_rows_n", {n: extra.length})),
          el("ul", {class: "steps"}, extra.map(function (r) {
            var dims = Object.keys(r.dims || {}).map(function (k) { return k + "=" + r.dims[k]; }).join(", ");
            return el("li", {class: "small"}, [dims + " ", value(r), " ", mstatus(r.status)]);
          }))]) : null;
        var prov = total && total.provenance || {};
        return el("tr", {class: (m.rows || []).length ? null : "row-deny"}, [
          can.sign ? el("td", {}, box) : null,
          el("td", {}, [KB.lang.pick(m, "name") || m.metric_id, " ", C.mono(m.metric_id, "small"), detail]),
          el("td", {}, (m.rows || []).length ? value(total) : el("span", {class: "chip status status-bad"}, t("mstatus_unavailable"))),
          el("td", {}, (m.statuses || []).length ? m.statuses.map(mstatus) : el("span", {class: "muted"}, "—")),
          el("td", {}, total ? C.levelChip(total.level, {compact: true}) : el("span", {class: "muted"}, "—")),
          el("td", {}, prov.signed_by ? [C.mono(prov.signed_by, "small"), prov.sql_sha256 ?
            el("span", {class: "muted small", title: prov.sql_sha256}, " sql " + String(prov.sql_sha256).slice(0, 10) + "…") : null] :
            el("span", {class: "muted"}, "—"))
        ]);
      });
      var anyVisible = (b.metrics || []).some(function (m) { return (m.rows || []).length; });
      if (!anyVisible) slot.appendChild(C.panel("muted", "lock", t("metrics_nothing_visible"), null));
      if (can.sign) slot.appendChild(actions(picked, period, state));
      var heads = (can.sign ? [t("metrics_select")] : []).concat([t("metrics_col_metric"), t("metrics_col_value"),
        t("metrics_col_status"), t("metrics_col_level"), t("metrics_col_signed")]);
      slot.appendChild(el("div", {class: "table-wrap"}, el("table", {class: "table"}, [
        el("thead", {}, el("tr", {}, heads.map(function (h) { return el("th", {scope: "col"}, h); }))),
        el("tbody", {}, rows)])));
    }).catch(function (e) {
      if (e.status === 401) return KB.app.sessionLost();
      U.clear(slot).appendChild(C.errorBox(e));
    });
  }

  function actions(picked, period, state) {
    function chosen() { return Object.keys(picked).filter(function (k) { return picked[k]; }); }
    function act(path, toastKey) {
      var ids = chosen();
      if (!ids.length) { U.toast(t("metrics_select_first"), "bad"); return; }
      KB.api.post(path, {period: period, metric_ids: ids}).then(function (r) {
        U.toast(t(toastKey, {n: (r.changed || []).length, s: (r.skipped || []).length}), "ok");
        state.reload();
      }).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        U.toast(e.serverMessage || e.code, "bad");
      });
    }
    var sign = el("button", {class: "btn btn-primary btn-sm", type: "button"}, [C.icon("check", "icon-sm"), t("metrics_sign")]);
    var pub = el("button", {class: "btn btn-outline btn-sm", type: "button"}, [C.icon("send", "icon-sm"), t("metrics_publish")]);
    sign.addEventListener("click", function () { act("/api/metrics/sign", "metrics_signed_toast"); });
    pub.addEventListener("click", function () { act("/api/metrics/publish", "metrics_published_toast"); });
    return el("div", {class: "row-actions"}, [sign, pub]);
  }

  // The CFO's demo refresh, outside the board: it must work when no period has snapshots yet (right
  // after `synth`) and for a month that has just ended, so it offers the server's list of ended
  // periods (`refreshable`), not the periods that already have rows.
  function refreshCard(list, current, done) {
    var opts = (list.refreshable || []).slice();
    if (!opts.length) return null;
    var selId = U.uid("metrics-refresh");
    var sel = el("select", {id: selId, class: "input input-sm"}, opts.map(function (p) {
      var o = el("option", {value: p}, p);
      if (p === current) o.selected = true;
      return o;
    }));
    var re = el("button", {class: "btn btn-ghost btn-sm", type: "button", title: t("metrics_refresh_hint")},
      [C.icon("refresh", "icon-sm"), t("metrics_refresh")]);
    re.addEventListener("click", function () {
      re.disabled = true;
      KB.api.post("/api/metrics/refresh", {period: sel.value}).then(function (r) {
        U.toast(t("metrics_refreshed_toast", {period: r.period, rows: r.rows}), "ok");
        done(r.period);
      }).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        U.toast(e.serverMessage || e.code, "bad");
      }).then(function () { re.disabled = false; });
    });
    return el("div", {class: "card pad form-inline"}, [el("label", {for: selId, class: "label"}, t("metrics_refresh_period")),
      sel, re, el("span", {class: "small muted"}, t("metrics_refresh_hint"))]);
  }

  KB.views = KB.views || {};
  KB.views.metrics = {
    render: function (root, args) {
      root.appendChild(C.pageHeader("h-metrics", "metrics_title", "metrics_sub"));
      var me = KB.state.me || {};
      if (me.role === "platform_admin" || me.role === "security_auditor") {
        root.appendChild(C.denied(t("metrics_denied_platform")));  // the server answers 403 as well
        return;
      }
      var top = el("div", {});
      var slot = el("div", {class: "result-slot", "aria-live": "polite"});
      root.appendChild(top);
      root.appendChild(whoCard());
      root.appendChild(slot);
      load(args && args[0]);

      // (re)reads the period list: after a refresh it may hold a period it did not have before
      function load(want) {
        U.clear(slot);
        U.clear(top).appendChild(C.loading());
        KB.api.get("/api/metrics").then(function (list) {
          U.clear(top);
          var state = {can: list.can || {}, period: null, reload: null};
          top.appendChild(roleNote(state.can));
          var periods = list.periods || [];
          if (state.can.refresh) {
            var rc = refreshCard(list, want || periods[0], load);
            if (rc) top.appendChild(rc);
          }
          if (!periods.length) {
            top.appendChild(C.panel("muted", "lock", t(state.can.refresh ? "metrics_no_periods_cfo" : "metrics_no_periods"), null));
            return;
          }
          var wanted = want && periods.indexOf(want) >= 0 ? want : periods[0];
          var selId = U.uid("metrics-period");
          var sel = el("select", {id: selId, class: "input input-sm"}, periods.map(function (p) {
            var o = el("option", {value: p}, p);
            if (p === wanted) o.selected = true;
            return o;
          }));
          state.reload = function () { board(slot, sel.value, state); };
          sel.addEventListener("change", state.reload);
          top.appendChild(el("div", {class: "card pad form-inline"}, [el("label", {for: selId, class: "label"}, t("metrics_period")), sel]));
          state.reload();
        }).catch(function (e) {
          if (e.status === 401) return KB.app.sessionLost();
          U.clear(top).appendChild(e.status === 403 ? C.denied(t("metrics_denied_platform")) : C.errorBox(e));
        });
      }
    }
  };
})();
