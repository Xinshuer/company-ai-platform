// Audit viewer (security auditor only). Shows the hash-chain status the server computed and the
// entries. Every load is itself audited server-side (event audit_read).
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  function chainBadge(chain) {
    chain = chain || {};
    if (chain.ok) {
      return el("div", {class: "chain chain-ok", role: "status"}, [C.icon("shieldcheck", "icon-sm"),
        el("div", {}, [el("strong", {}, t("chain_ok")), el("span", {class: "small"}, " " + t("chain_checked", {n: chain.checked}))])]);
    }
    return el("div", {class: "chain chain-bad", role: "alert"}, [C.icon("alert", "icon-sm"),
      el("div", {}, [el("strong", {}, t("chain_broken")),
        el("span", {class: "small"}, " " + t("chain_first_bad", {seq: chain.first_bad_seq === null || chain.first_bad_seq === undefined ? "?" : chain.first_bad_seq}))])]);
  }

  function row(e) {
    var tr = el("tr", {class: e.decision === "deny" ? "row-deny" : ""}, [
      el("td", {class: "mono"}, String(e.seq)),
      el("td", {class: "nowrap small"}, U.fmtTime(e.ts)),
      el("td", {}, [C.mono(e.actor || "—", "small"), (e.act_chain || []).length ? el("div", {class: "small muted"}, e.act_chain.join(" › ")) : null]),
      el("td", {}, [el("span", {class: "pill"}, e.event)]),
      el("td", {class: "small"}, [e.action ? C.mono(e.action, "small") : null, e.resource ? el("div", {class: "muted"}, e.resource) : null]),
      el("td", {}, C.decisionChip(e.decision)),
      el("td", {class: "small"}, e.reason ? C.reasonText(e.reason) : "—"),
      el("td", {}, e.route ? C.routeBadge(e.route) : "—"),
      el("td", {}, e.level ? C.levelChip(e.level, {compact: true}) : "—"),
      el("td", {}, [Object.keys(e.details || {}).length || e.run_id ? (function () {
        var d = el("details", {class: "raw"}, [el("summary", {}, t("details")),
          e.run_id ? el("p", {class: "small"}, [t("run_id") + " ", C.mono(e.run_id, "small")]) : null,
          C.jsonBlock(e.details || {})]);
        return d;
      })() : "—"])
    ]);
    return tr;
  }

  KB.views = KB.views || {};
  KB.views.audit = {
    render: function (root) {
      var me = KB.state.me;
      root.appendChild(C.pageHeader("h-audit", "nav_audit", "audit_sub"));
      if (!(me.can && me.can.audit)) {
        root.appendChild(C.denied(t("audit_denied")));
        return;
      }
      root.appendChild(el("p", {class: "small muted"}, [C.icon("info", "icon-xs"), t("audit_read_note")]));

      var chainSlot = el("div");
      var filters = el("div", {class: "filters card pad"});
      var evSel = el("select", {id: "audit-ev", class: "input input-sm"}, [el("option", {value: ""}, t("all"))]);
      var decSel = el("select", {id: "audit-dec", class: "input input-sm"}, [el("option", {value: ""}, t("all")),
        el("option", {value: "allow"}, t("decision_allow")), el("option", {value: "deny"}, t("decision_deny")),
        el("option", {value: "reroute"}, t("decision_reroute")), el("option", {value: "error"}, t("decision_error"))]);
      var q = el("input", {id: "audit-q", class: "input input-sm", type: "search", placeholder: t("audit_search_ph")});
      filters.appendChild(el("div", {class: "filter"}, [el("label", {for: "audit-ev", class: "small"}, t("col_event")), evSel]));
      filters.appendChild(el("div", {class: "filter"}, [el("label", {for: "audit-dec", class: "small"}, t("col_decision")), decSel]));
      filters.appendChild(el("div", {class: "filter grow"}, [el("label", {for: "audit-q", class: "small"}, t("audit_search")), q]));
      var tamper = null;
      if (KB.api.mode === "demo") {
        tamper = el("button", {class: "btn btn-ghost btn-sm", type: "button", "aria-pressed": KB.demo.isTampered() ? "true" : "false"},
          t(KB.demo.isTampered() ? "audit_untamper" : "audit_tamper"));
        tamper.addEventListener("click", function () { KB.demo.setTampered(!KB.demo.isTampered()); KB.router.refresh(); });
        filters.appendChild(el("div", {class: "filter"}, [el("span", {class: "small muted"}, t("demo_only")), tamper]));
      }

      var tbody = el("tbody");
      var table = el("div", {class: "table-wrap"}, [el("table", {class: "table audit"}, [
        el("caption", {class: "sr-only"}, t("nav_audit")),
        el("thead", {}, [el("tr", {}, ["col_seq", "col_time", "col_actor", "col_event", "col_action", "col_decision",
          "col_reason", "col_route", "col_level", "details"].map(function (k) { return el("th", {scope: "col"}, t(k)); }))]),
        tbody
      ])]);
      var more = el("button", {class: "btn btn-outline btn-sm", type: "button"}, t("load_more"));
      var status = el("p", {class: "small muted", "aria-live": "polite"});

      root.appendChild(chainSlot);
      root.appendChild(filters);
      root.appendChild(table);
      root.appendChild(el("div", {class: "row-actions"}, [more, status]));

      var all = [];
      var lastSeq = 0;
      function paint() {
        var ev = evSel.value, dec = decSel.value, needle = q.value.trim().toLowerCase();
        U.clear(tbody);
        var shown = all.filter(function (e) {
          if (ev && e.event !== ev) return false;
          if (dec && e.decision !== dec) return false;
          if (needle) {
            var hay = [e.actor, e.action, e.resource, e.reason, e.run_id, e.approval_id].join(" ").toLowerCase();
            if (hay.indexOf(needle) < 0) return false;
          }
          return true;
        });
        shown.forEach(function (e) { tbody.appendChild(row(e)); });
        status.textContent = t("audit_shown", {n: shown.length, total: all.length});
      }
      function load() {
        more.disabled = true;
        KB.api.get("/api/audit?after_seq=" + lastSeq + "&limit=200").then(function (res) {
          var entries = (res && res.entries) || [];
          all = all.concat(entries);
          if (entries.length) lastSeq = entries[entries.length - 1].seq;
          U.clear(chainSlot).appendChild(chainBadge(res && res.chain));
          var events = {};
          all.forEach(function (e) { events[e.event] = true; });
          var cur = evSel.value;
          while (evSel.options.length > 1) evSel.remove(1);
          Object.keys(events).sort().forEach(function (k) { evSel.appendChild(el("option", {value: k}, k)); });
          evSel.value = cur;
          more.disabled = entries.length < 200;
          paint();
        }).catch(function (e) {
          if (e.status === 401) return KB.app.sessionLost();
          more.disabled = false;
          U.clear(chainSlot).appendChild(C.errorBox(e));
        });
      }
      [evSel, decSel].forEach(function (s) { s.addEventListener("change", paint); });
      q.addEventListener("input", paint);
      more.addEventListener("click", load);
      load();
    }
  };
})();
