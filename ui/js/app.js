// Boot: theme and language, mode detection, session, header controls, first render.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  function t(k, v) { return KB.lang.t(k, v); }

  KB.state = {me: null, chat: null, recentRuns: [], approvalsBox: null, ready: false};

  var A = (KB.app = {});

  // ---------------------------------------------------------------- theme
  function systemDark() {
    try { return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches; } catch (e) { return false; }
  }
  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
    var dark = theme === "dark" || (theme !== "light" && systemDark());
    var btn = document.getElementById("btn-theme");
    if (btn) {
      U.clear(btn).appendChild(KB.icon(dark ? "sun" : "moon"));
      btn.setAttribute("aria-label", t(dark ? "theme_to_light" : "theme_to_dark"));
      btn.setAttribute("title", t(dark ? "theme_to_light" : "theme_to_dark"));
    }
  }
  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme");
    var dark = cur === "dark" || (cur !== "light" && systemDark());
    var next = dark ? "light" : "dark";
    U.store.set("theme", next);
    applyTheme(next);
  }

  // ---------------------------------------------------------------- header
  A.updateModeBadge = function (switches) {
    var b = document.getElementById("mode-badge");
    var m = KB.api.mode;
    U.clear(b);
    b.className = "mode-badge mode-" + m;
    b.appendChild(U.el("span", {class: "mode-dot", "aria-hidden": "true"}));
    b.appendChild(document.createTextNode(t(m === "live" ? "mode_live" : "mode_demo")));
    b.setAttribute("title", t("mode_reason_" + KB.api.reason));
    if (switches) KB.api.noteSwitches(switches);  // remember what the admin page just set
    var sw = switches || (m === "demo" ? KB.demo.switches() : (KB.api.health && KB.api.health.switches)) || {};
    var banner = document.getElementById("global-banner");
    var notes = [];
    if (sw.local_down) notes.push(t("banner_local_down"));
    if (sw.external_down) notes.push(t("banner_external_down"));
    U.clear(banner);
    banner.hidden = !notes.length;
    notes.forEach(function (n) { banner.appendChild(U.el("p", {}, [KB.icon("alert", "icon-sm"), n])); });
  };

  // Live: the switches can change in another tab or by another admin; ask the server again
  // before repainting the banner instead of trusting what was fetched at page load.
  A.refreshBanner = function () {
    if (KB.api.mode !== "live") { A.updateModeBadge(); return Promise.resolve(); }
    return KB.api.refreshHealth().then(function () { A.updateModeBadge(); });
  };

  function updateWho() {
    var me = KB.state.me;
    var who = document.getElementById("who");
    if (!me) { who.hidden = true; return; }
    who.hidden = false;
    var av = document.getElementById("who-avatar");
    av.className = "avatar avatar-sm dept-" + me.dept;
    av.textContent = U.initials(me.name);
    document.getElementById("who-name").textContent = me.name;
    document.getElementById("who-title").textContent = me.title;
  }

  // ---------------------------------------------------------------- session
  function resetSessionState() {
    KB.state.chat = null;
    KB.state.recentRuns = [];
    KB.state.approvalsBox = null;
  }

  A.loadMe = function () {
    return KB.api.get("/api/me").then(function (me) {
      KB.state.me = me || null;
      updateWho();
      return me;
    }, function (e) {
      KB.state.me = null;
      updateWho();
      if (e.status !== 401) U.toast(KB.lang.has("err_" + e.code) ? t("err_" + e.code) : t("err_unknown"), "bad");
      return null;
    });
  };

  A.login = function (userId) {
    return KB.api.post("/api/auth/demo-login", {user_id: userId}).then(function () {
      resetSessionState();
      A.refreshBanner();
      return A.loadMe();
    }).then(function (me) {
      if (!me) throw Object.assign(new Error("login"), {code: "unauthenticated", status: 401});
      U.toast(t("logged_in_as", {name: me.name}), "ok");
      KB.router.go("home");
    });
  };

  A.logout = function () {
    return KB.api.post("/api/auth/logout", {}).catch(function () { /* already gone */ }).then(function () {
      KB.state.me = null;
      resetSessionState();
      updateWho();
      KB.router.go("login");
    });
  };

  A.sessionLost = function () {
    KB.state.me = null;
    resetSessionState();
    updateWho();
    U.toast(t("session_lost"), "bad");
    KB.router.go("login");
  };

  // The trace page's recent list: only runs whose trace the current user can open.
  A.rememberRun = function (run, label) {
    if (!run || !run.run_id || !KB.c.canTrace(run)) return;
    var list = KB.state.recentRuns.filter(function (r) { return r.run_id !== run.run_id; });
    list.unshift({run_id: run.run_id, route: run.route, label: String(label || "").slice(0, 28)});
    KB.state.recentRuns = list.slice(0, 12);
  };

  function rerender() {
    KB.lang.apply(document);
    A.updateModeBadge();
    A.refreshBanner();
    updateWho();
    if (KB.state.ready) KB.router.render();
  }

  function boot() {
    KB.lang.current = U.store.get("lang") === "en" ? "en" : "zh";
    document.documentElement.lang = KB.lang.current === "zh" ? "zh-CN" : "en";
    applyTheme(U.store.get("theme"));
    KB.hydrateIcons(document);
    KB.lang.apply(document);
    var langBtn = document.getElementById("btn-lang");
    langBtn.textContent = KB.lang.current === "zh" ? "EN" : "中";

    langBtn.addEventListener("click", function () {
      KB.lang.set(KB.lang.current === "zh" ? "en" : "zh");
      langBtn.textContent = KB.lang.current === "zh" ? "EN" : "中";
      rerender();
    });
    document.getElementById("btn-theme").addEventListener("click", toggleTheme);
    document.getElementById("btn-switch").addEventListener("click", A.logout);
    window.addEventListener("hashchange", function () { A.refreshBanner(); });
    try {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
        applyTheme(U.store.get("theme"));
      });
    } catch (e) { /* old browsers: the toggle still works */ }
    start();
  }

  function start() {
    KB.api.init().then(function () {
      A.updateModeBadge();
      return A.loadMe();
    }).then(function () {
      KB.state.ready = true;
      KB.router.render();
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
