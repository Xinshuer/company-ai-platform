// Outage switches (platform admin only). Every change needs a reason and is audited
// (event outage_switch). The page states what each switch does and what it never does:
// local_down never sends confidential traffic to the external route.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var SWITCHES = [
    {name: "local_down", icon: "cpu"},
    {name: "external_down", icon: "cloud"}
  ];

  function card(sw, state, onChange) {
    var on = !!state[sw.name];
    var reasonId = U.uid("sw-reason");
    var reason = el("input", {id: reasonId, class: "input", type: "text", maxlength: "300",
                              placeholder: t("switch_reason_ph"), required: true});
    var toggle = el("button", {class: "switch" + (on ? " on" : ""), type: "button", role: "switch",
                               "aria-checked": on ? "true" : "false", "aria-label": t("sw_" + sw.name)},
      [el("span", {class: "switch-knob", "aria-hidden": "true"})]);
    var out = el("div", {"aria-live": "polite"});
    toggle.addEventListener("click", function () {
      var why = reason.value.trim();
      if (!why) {
        reason.setAttribute("aria-invalid", "true");
        reason.focus();
        U.toast(t("reason_required"), "bad");
        return;
      }
      reason.removeAttribute("aria-invalid");
      toggle.disabled = true;
      KB.api.post("/api/admin/switches", {name: sw.name, on: !on, reason: why}).then(function (res) {
        U.toast(t(!on ? "switch_on_toast" : "switch_off_toast", {name: t("sw_" + sw.name)}), "ok");
        onChange(res);
      }).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        toggle.disabled = false;
        U.clear(out).appendChild(C.errorBox(e));
      });
    });
    return el("article", {class: "card switch-card" + (on ? " is-on" : "")}, [
      el("div", {class: "switch-head"}, [
        el("span", {class: "app-icon"}, [C.icon(sw.icon)]),
        el("div", {class: "grow"}, [
          el("h2", {class: "card-title"}, t("sw_" + sw.name)),
          el("p", {class: "small mono muted"}, sw.name)
        ]),
        el("span", {class: "chip status " + (on ? "status-bad" : "status-ok")}, t(on ? "switch_on" : "switch_off")),
        toggle
      ]),
      el("dl", {class: "kv"}, [
        el("dt", {}, t("switch_does")), el("dd", {}, t("sw_" + sw.name + "_does")),
        el("dt", {}, t("switch_never")), el("dd", {}, t("sw_" + sw.name + "_never")),
        el("dt", {}, t("switch_audit")), el("dd", {}, t("sw_audit_text"))
      ]),
      el("label", {for: reasonId, class: "label"}, t("switch_reason")),
      reason,
      out
    ]);
  }

  KB.views = KB.views || {};
  KB.views.admin = {
    render: function (root) {
      var me = KB.state.me;
      root.appendChild(C.pageHeader("h-admin", "nav_admin", "admin_sub"));
      if (!(me.can && me.can.admin)) {
        root.appendChild(C.denied(t("admin_denied")));
        return;
      }
      var grid = el("div", {class: "switch-grid"}, [C.loading()]);
      root.appendChild(grid);
      root.appendChild(C.panel("info", "info", t("admin_try_title"), [el("p", {}, t("admin_try_text"))]));
      function paint(state) {
        U.clear(grid);
        SWITCHES.forEach(function (sw) { grid.appendChild(card(sw, state || {}, paint)); });
        KB.app.updateModeBadge(state);
      }
      KB.api.get("/api/admin/switches").then(paint).catch(function (e) {
        if (e.status === 401) return KB.app.sessionLost();
        U.clear(grid).appendChild(C.errorBox(e));
      });
    }
  };
})();
