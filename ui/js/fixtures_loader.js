// Loads ui/fixtures/*.js, and only for demo mode. The fixtures are recorded runs, proposals,
// traces and audit rows, some of them confidential sample answers: the live server never serves
// them (404), and the page never asks for them while it talks to a live API. Classic scripts
// added in order (async = false), so the page still works when opened from disk.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});

  // every file in ui/fixtures/ (tests/ui/test_static.py keeps this list complete)
  var NAMES = ["personas", "me", "health", "switches", "runs", "compare", "proposals", "traces", "audit"];
  var pending = null;

  KB.fixtures = {
    names: NAMES.slice(),
    // Resolves once every file has loaded or failed; never rejects (a missing file = no data).
    load: function () {
      if (pending) return pending;
      pending = Promise.all(NAMES.map(function (name) {
        return new Promise(function (resolve) {
          var s = document.createElement("script");
          s.src = "fixtures/" + name + ".js";
          s.async = false;
          s.onload = function () { resolve(true); };
          s.onerror = function () { resolve(false); };
          document.head.appendChild(s);
        });
      }));
      return pending;
    }
  };
})();
