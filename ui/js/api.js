// Mode detection and the one request function every view uses.
// file:  → demo (fixtures). http(s) → GET /api/health; live if it answers ok, else demo.
// The demo fixtures are loaded only once the mode is demo: a live page never requests them (the
// live server answers 404 for them anyway), and there is no "force demo" on a live server, so
// nobody logged in there can read the recorded sample answers of other personas.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;

  var A = (KB.api = {mode: "demo", reason: "file", health: null});

  function apiError(status, code, message) {
    var e = new Error(message || code);
    e.status = status;
    e.code = code;
    e.serverMessage = message || "";
    return e;
  }

  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(apiError(0, "network", "timeout")); }, ms);
      promise.then(function (v) { clearTimeout(timer); resolve(v); },
                   function (e) { clearTimeout(timer); reject(e); });
    });
  }

  function live(method, path, body) {
    var opts = {
      method: method,
      // the session cookie is HttpOnly and SameSite=Strict; the page never sees a token
      credentials: "same-origin",
      headers: {"Accept": "application/json"}
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return withTimeout(window.fetch(path, opts), method === "POST" ? 180000 : 20000)
      .catch(function (e) {
        if (e && e.code) throw e;
        throw apiError(0, "network", "network error");
      })
      .then(function (res) {
        return res.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
          if (!res.ok) {
            var er = (data && data.error) || {};
            throw apiError(res.status, er.code || ("http_" + res.status), er.message || "");
          }
          return data;
        });
      });
  }

  A.request = function (method, path, body) {
    if (A.mode === "live") return live(method, path, body);
    return KB.demo.request(method, path, body);
  };
  A.get = function (path) { return A.request("GET", path); };
  A.post = function (path, body) { return A.request("POST", path, body || {}); };

  A.isFile = function () { return window.location.protocol === "file:"; };

  function demo(reason) {
    A.mode = "demo";
    A.reason = reason;
    return KB.fixtures.load().then(function () {
      KB.demo.reload();
      KB.demo.restore();
      return A.mode;
    });
  }

  // Resolves to the mode. Never rejects: anything unexpected means demo.
  A.init = function () {
    U.store.set("forceDemo", null);  // the old "force demo" toggle is gone; drop its leftover flag
    if (A.isFile()) return demo("file");
    return withTimeout(window.fetch("/api/health", {credentials: "same-origin", headers: {"Accept": "application/json"}}), 3000)
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (h) {
        if (h && h.ok) {
          A.mode = "live";
          A.reason = "health";
          A.health = h;
          return A.mode;
        }
        return demo("no_backend");
      }, function () { return demo("no_backend"); });
  };

  // The latest outage switches seen (admin page, health refresh), for the header banner.
  A.refreshHealth = function () {
    if (A.mode !== "live") return Promise.resolve(A.health);
    return live("GET", "/api/health").then(function (h) {
      if (h && h.ok) A.health = h;
      return A.health;
    }, function () { return A.health; });
  };
  A.noteSwitches = function (switches) {
    if (A.mode === "live" && switches) A.health = Object.assign({}, A.health || {}, {switches: switches});
  };
})();
