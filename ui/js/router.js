// Hash router. Each route shows exactly one <section id="view-…"> from index.html;
// tests/ui/test_static.py checks that every view id listed here exists in the page.
// Every milestone route is listed here already and rendered by its own view_<name>.js, so a milestone
// never edits this file; deeper pages use the route's args (#/legal/<review_id> → args ["<review_id>"]).
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  function t(k, v) { return KB.lang.t(k, v); }

  // nav: where the route appears in the side navigation ("apps" only when the persona has the app);
  // notRoles: roles the page always refuses (it still says so explicitly when opened directly)
  KB.ROUTES = [
    {path: "login", view: "view-login", auth: false},
    {path: "home", view: "view-home", nav: "main", icon: "home", label: "nav_home"},
    {path: "qa", view: "view-qa", nav: "apps", icon: "chat", app: "qa"},
    {path: "legal", view: "view-legal", nav: "apps", icon: "scale", app: "legal_review"},
    {path: "report", view: "view-report", nav: "apps", icon: "chart", app: "report"},
    {path: "sales", view: "view-sales", nav: "apps", icon: "target", app: "sales_profile"},
    {path: "headcount", view: "view-headcount", nav: "apps", icon: "users", app: "hr_headcount"},
    {path: "hr", view: "view-hr", nav: "apps", icon: "usercheck", app: "hr_screening"},
    {path: "candidates", view: "view-candidates", nav: "apps", icon: "search", app: "hr_candidates"},
    {path: "approvals", view: "view-approvals", nav: "work", icon: "inbox", label: "nav_approvals"},
    {path: "compare", view: "view-compare", nav: "work", icon: "columns", label: "nav_compare"},
    {path: "metrics", view: "view-metrics", nav: "work", icon: "trending", label: "nav_metrics",
     notRoles: ["platform_admin", "security_auditor"]},
    {path: "trace", view: "view-trace", nav: "work", icon: "activity", label: "nav_trace"},
    {path: "audit", view: "view-audit", nav: "ops", icon: "shieldcheck", label: "nav_audit", need: "audit"},
    {path: "admin", view: "view-admin", nav: "ops", icon: "toggle", label: "nav_admin", need: "admin"},
    {path: "notfound", view: "view-notfound", auth: false}
  ];

  var R = (KB.router = {current: null, args: []});

  function find(path) {
    for (var i = 0; i < KB.ROUTES.length; i++) if (KB.ROUTES[i].path === path) return KB.ROUTES[i];
    return null;
  }

  function parse() {
    var h = (window.location.hash || "").replace(/^#\/?/, "");
    var parts = h.split("/").filter(function (x) { return x !== ""; });
    return {path: parts[0] || "", args: parts.slice(1)};
  }

  R.navLabel = function (route) {
    if (route.app) {
      var me = KB.state.me;
      var a = me && (me.apps || []).filter(function (x) { return x.id === route.app; })[0];
      return a ? KB.lang.pick(a, "title") : t("app_" + route.app);
    }
    return t(route.label);
  };

  R.buildNav = function () {
    var nav = document.getElementById("sidenav");
    U.clear(nav);
    var me = KB.state.me;
    if (!me) { nav.hidden = true; return; }
    nav.hidden = false;
    var apps = {};
    (me.apps || []).forEach(function (a) { apps[a.id] = true; });
    var can = me.can || {};
    [["main", null], ["apps", "nav_group_apps"], ["work", "nav_group_work"], ["ops", "nav_group_ops"]].forEach(function (g) {
      var items = KB.ROUTES.filter(function (r) {
        if (r.nav !== g[0]) return false;
        if (r.app && !apps[r.app]) return false;
        if (r.need && !can[r.need]) return false;
        if (r.notRoles && r.notRoles.indexOf(me.role) >= 0) return false;
        return true;
      });
      if (!items.length) return;
      if (g[1]) nav.appendChild(el("p", {class: "nav-group"}, t(g[1])));
      nav.appendChild(el("ul", {class: "nav-list"}, items.map(function (r) {
        var active = R.current === r.path;
        return el("li", {}, [el("a", {href: "#/" + r.path, class: "nav-link" + (active ? " active" : ""),
                                       "aria-current": active ? "page" : null},
          [KB.icon(r.icon, "icon-sm"), el("span", {}, R.navLabel(r))])]);
      })));
    });
  };

  R.render = function () {
    var p = parse();
    var me = KB.state.me;
    var route = find(p.path);
    if (!p.path) route = find(me ? "home" : "login");
    if (!route) route = find("notfound");
    if (route.auth !== false && !me) {
      route = find("login");
    } else if (route.path === "login" && me) {
      route = find("home");
    }
    R.current = route.path;
    R.args = p.args;
    KB.ROUTES.forEach(function (r) {
      var sec = document.getElementById(r.view);
      if (sec) sec.hidden = r.view !== route.view;
    });
    var root = document.getElementById(route.view);
    U.clear(root);
    var view = KB.views && KB.views[route.path];
    if (view) {
      try {
        view.render(root, p.args);
      } catch (e) {
        root.appendChild(KB.c.errorBox({code: "unknown", serverMessage: String(e && e.message || e)}));
      }
    }
    KB.lang.apply(root);
    R.buildNav();
    // move focus to the page heading so screen readers announce the new view
    var h = root.querySelector("h1");
    if (h && R._navigated) h.focus({preventScroll: true});
    R._navigated = true;
    window.scrollTo(0, 0);
  };

  R.refresh = function () { R.render(); };

  R.go = function (path) {
    if (window.location.hash === "#/" + path) R.render();
    else window.location.hash = "#/" + path;
  };

  window.addEventListener("hashchange", function () { if (KB.state && KB.state.ready) R.render(); });

  KB.views = KB.views || {};
  KB.views.notfound = {
    render: function (root) {
      root.appendChild(KB.c.pageHeader("h-notfound", "notfound_title", "notfound_sub"));
      root.appendChild(el("p", {}, [el("a", {href: "#/home", class: "btn btn-outline"}, t("nav_home"))]));
    }
  };
})();
