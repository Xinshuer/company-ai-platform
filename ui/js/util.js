// DOM and formatting helpers. Every piece of data reaches the page through text nodes:
// nothing from the API or fixtures is ever assigned to innerHTML (answers, snippets and
// web text can carry prompt-injection or markup).
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = (KB.util = {});

  function append(node, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
      child.forEach(function (c) { append(node, c); });
      return;
    }
    if (typeof child === "string" || typeof child === "number") {
      node.appendChild(document.createTextNode(String(child)));
      return;
    }
    node.appendChild(child);
  }

  // el("div", {class: "x", on: {click: fn}, "aria-label": "…"}, [children])
  U.el = function (tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "on") {
        Object.keys(v).forEach(function (ev) { node.addEventListener(ev, v[ev]); });
      } else if (k === "text") {
        node.textContent = String(v);
      } else if (k === "class") {
        node.className = v;
      } else if (k === "dataset") {
        Object.keys(v).forEach(function (d) { node.dataset[d] = v[d]; });
      } else if (k === "style") {
        node.setAttribute("style", v);
      } else if (v === true) {
        node.setAttribute(k, "");
      } else {
        node.setAttribute(k, String(v));
      }
    });
    append(node, children);
    return node;
  };

  U.append = append;

  U.clear = function (node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  };

  U.copy = function (obj) {
    return obj === undefined ? undefined : JSON.parse(JSON.stringify(obj));
  };

  // Per-viewer conveniences only (language, theme, forced demo, last persona in demo).
  // Storage may be blocked (private windows, file:// in some browsers): never required.
  U.store = {
    get: function (k) {
      try { return window.localStorage.getItem("kb." + k); } catch (e) { return null; }
    },
    set: function (k, v) {
      try {
        if (v === null || v === undefined) window.localStorage.removeItem("kb." + k);
        else window.localStorage.setItem("kb." + k, String(v));
      } catch (e) { /* storage blocked: the page works without it */ }
    }
  };

  // Timestamps are shown in UTC so the demo reads the same on every machine.
  U.fmtTime = function (iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var s = d.toISOString();
    return s.slice(0, 10) + " " + s.slice(11, 19) + " UTC";
  };

  U.fmtShortTime = function (iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toISOString().slice(11, 23);
  };

  U.ms = function (a, b) {
    var x = new Date(a).getTime();
    var y = new Date(b).getTime();
    if (isNaN(x) || isNaN(y)) return null;
    return Math.max(0, y - x);
  };

  U.fmtMs = function (ms) {
    if (ms === null || ms === undefined) return "—";
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(2) + " s";
  };

  U.json = function (v) {
    try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
  };

  U.initials = function (name) {
    var parts = String(name || "?").trim().split(/\s+/);
    var a = parts[0] ? parts[0].charAt(0) : "?";
    var b = parts.length > 1 ? parts[parts.length - 1].charAt(0) : "";
    return (a + b).toUpperCase();
  };

  U.isSafeHttpUrl = function (url) {
    return /^https?:\/\/[^\s<>"']+$/i.test(String(url || ""));
  };

  // External links always show the full URL and ask before leaving the page (architecture §14).
  U.extLink = function (url, label) {
    if (!U.isSafeHttpUrl(url)) return document.createTextNode(String(label || url || ""));
    var a = U.el("a", {
      class: "ext-link",
      href: url,
      rel: "noopener noreferrer nofollow",
      target: "_blank",
      title: url
    }, [
      label && label !== url ? U.el("span", {class: "ext-label", text: label}) : null,
      U.el("span", {class: "ext-url mono", text: url}),
      KB.icon ? KB.icon("external", "icon-xs") : null
    ]);
    a.addEventListener("click", function (ev) {
      ev.preventDefault();
      var msg = KB.lang.t("confirm_external", {url: url});
      if (window.confirm(msg)) window.open(url, "_blank", "noopener,noreferrer");
    });
    return a;
  };

  // Plain text with URLs turned into confirm-before-open links. Newlines are kept by CSS.
  U.linkify = function (text) {
    var out = [];
    var s = String(text || "");
    var re = /https?:\/\/[^\s<>"'，。；）)]+/gi;
    var last = 0;
    var m;
    while ((m = re.exec(s)) !== null) {
      if (m.index > last) out.push(document.createTextNode(s.slice(last, m.index)));
      var url = m[0].replace(/[.,;:!?]+$/, "");
      out.push(U.extLink(url));
      last = m.index + url.length;
      re.lastIndex = last;
    }
    if (last < s.length) out.push(document.createTextNode(s.slice(last)));
    return out;
  };

  U.toast = function (msg, kind) {
    var t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.className = "toast" + (kind ? " toast-" + kind : "");
    t.hidden = false;
    clearTimeout(U._toastTimer);
    U._toastTimer = setTimeout(function () { t.hidden = true; }, 3800);
  };

  U.uid = (function () {
    var n = 0;
    return function (p) { n += 1; return (p || "id") + "-" + n; };
  })();
})();
