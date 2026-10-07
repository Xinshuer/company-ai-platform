// Role home: who you are, the apps this persona may use (from /api/me.apps), and the
// ones it may not, shown locked so the permission model is visible rather than hidden.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var el = KB.util.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  // app id → route, icon and the milestone that delivers its full graph (architecture §16)
  KB.APP_CATALOG = [
    {id: "qa", route: "qa", icon: "chat", milestone: "M1"},
    {id: "legal_review", route: "legal", icon: "scale", milestone: "M2"},
    {id: "report", route: "report", icon: "chart", milestone: "M3"},
    {id: "sales_profile", route: "sales", icon: "target", milestone: "M4"},
    {id: "hr_headcount", route: "headcount", icon: "users", milestone: "M4"},
    {id: "hr_screening", route: "hr", icon: "usercheck", milestone: "M4"}
  ];

  function appCard(app, meApp) {
    var allowed = !!meApp;
    var title = meApp ? KB.lang.pick(meApp, "title") : t("app_" + app.id);
    var inner = [
      el("span", {class: "app-icon"}, [C.icon(allowed ? app.icon : "lock")]),
      el("span", {class: "app-text"}, [
        el("span", {class: "app-title"}, title),
        el("span", {class: "app-desc"}, t("app_" + app.id + "_desc"))
      ]),
      el("span", {class: "app-foot"}, [
        el("span", {class: "pill"}, app.milestone),
        allowed ? null : el("span", {class: "pill pill-bad"}, t("not_for_your_role"))
      ])
    ];
    if (allowed) return el("a", {class: "app-card", href: "#/" + app.route}, inner);
    return el("div", {class: "app-card locked", "aria-disabled": "true", title: t("not_for_your_role")}, inner);
  }

  function toolCard(route, icon, titleKey, descKey, allowed, badge) {
    var inner = [
      el("span", {class: "app-icon"}, [C.icon(allowed ? icon : "lock")]),
      el("span", {class: "app-text"}, [
        el("span", {class: "app-title"}, [t(titleKey), badge || null]),
        el("span", {class: "app-desc"}, t(descKey))
      ])
    ];
    if (allowed) return el("a", {class: "app-card tool", href: "#/" + route}, inner);
    return el("div", {class: "app-card tool locked", "aria-disabled": "true"}, inner);
  }

  KB.views = KB.views || {};
  KB.views.home = {
    render: function (root) {
      var me = KB.state.me;
      var can = me.can || {};
      var byId = {};
      (me.apps || []).forEach(function (a) { byId[a.id] = a; });

      root.appendChild(el("div", {class: "hero card"}, [
        C.avatar(me.name, me.dept, "lg"),
        el("div", {class: "hero-text"}, [
          el("h1", {id: "h-home", tabindex: "-1"}, t("home_hello", {name: me.name.split(" ")[0]})),
          el("p", {class: "hero-sub"}, [me.title, " · ", t("dept_" + me.dept)]),
          el("div", {class: "hero-tags"}, [C.roleBadge(me.role), C.levelChip(me.clearance, {prefix: t("clearance_short")})]),
          el("p", {class: "small muted"}, t("home_clearance_hint", {level: "L" + me.clearance}))
        ])
      ]));

      root.appendChild(el("h2", {class: "section-title"}, t("home_apps")));
      root.appendChild(el("div", {class: "app-grid"}, KB.APP_CATALOG.map(function (a) { return appCard(a, byId[a.id]); })));

      var badge = el("span", {class: "count-badge", hidden: true});
      root.appendChild(el("h2", {class: "section-title"}, t("home_tools")));
      root.appendChild(el("div", {class: "app-grid"}, [
        toolCard("approvals", "inbox", "nav_approvals", "tool_approvals_desc", true, badge),
        toolCard("compare", "columns", "nav_compare", "tool_compare_desc", true),
        toolCard("trace", "activity", "nav_trace", "tool_trace_desc", true),
        toolCard("audit", "shieldcheck", "nav_audit", can.audit ? "tool_audit_desc" : "tool_audit_locked", !!can.audit),
        toolCard("admin", "toggle", "nav_admin", can.admin ? "tool_admin_desc" : "tool_admin_locked", !!can.admin)
      ]));

      if (can.approve) {
        KB.api.get("/api/proposals?box=to_approve").then(function (list) {
          var n = (list || []).filter(function (p) { return p.status === "pending"; }).length;
          if (n) { badge.textContent = String(n); badge.hidden = false; badge.setAttribute("aria-label", t("pending_n", {n: n})); }
        }).catch(function () { /* badge is optional */ });
      }

      root.appendChild(el("h2", {class: "section-title"}, t("home_how")));
      root.appendChild(el("ol", {class: "how-grid"}, [
        ["tag", "how_1_title", "how_1_text"], ["route", "how_2_title", "how_2_text"],
        ["inbox", "how_3_title", "how_3_text"], ["shieldcheck", "how_4_title", "how_4_text"]
      ].map(function (h, i) {
        return el("li", {class: "how"}, [
          el("span", {class: "how-num", "aria-hidden": "true"}, String(i + 1)),
          el("span", {class: "how-icon"}, [C.icon(h[0])]),
          el("strong", {}, t(h[1])),
          el("p", {class: "small"}, t(h[2]))
        ]);
      })));
    }
  };
})();
