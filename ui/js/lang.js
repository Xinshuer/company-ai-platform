// Language runtime. Shared strings live in i18n.js (window.I18N.zh / .en, identical key sets);
// each milestone adds its own in i18n_<name>.js through L.extend, loaded after this file.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var dict = window.I18N || {zh: {}, en: {}};

  var L = (KB.lang = {current: "zh"});

  // t("key", {name: "x"}) → string with {name} replaced; falls back to zh, then the key itself
  // so a missing string is visible instead of blank.
  L.t = function (key, vars) {
    var table = dict[L.current] || dict.zh;
    var s = table[key];
    if (s === undefined) s = dict.zh[key];
    if (s === undefined) return key;
    if (vars) {
      s = s.replace(/\{(\w+)\}/g, function (m, k) {
        return vars[k] === undefined || vars[k] === null ? m : String(vars[k]);
      });
    }
    return s;
  };

  // extend("legal", {zh: {...}, en: {...}}): merge a milestone's strings into the dictionary. A key
  // that already exists is refused (kept as it was), so one milestone cannot silently reword
  // another's or a shared string; the static test fails on such a collision before it ships.
  L.extended = [];
  L.extend = function (name, pack) {
    ["zh", "en"].forEach(function (lang) {
      var target = dict[lang] || (dict[lang] = {});
      var add = (pack && pack[lang]) || {};
      Object.keys(add).forEach(function (k) {
        if (Object.prototype.hasOwnProperty.call(target, k)) {
          if (window.console) window.console.warn("i18n_" + name + ": key already defined, ignored: " + k);
          return;
        }
        target[k] = add[k];
      });
    });
    L.extended.push(name);
  };

  L.has = function (key) {
    return Object.prototype.hasOwnProperty.call(dict.zh, key);
  };

  // pick({zh: "…", en: "…"}) or pick(obj, "title") for title_zh / title_en pairs from the API
  L.pick = function (obj, base) {
    if (!obj) return "";
    if (base) return obj[base + "_" + L.current] || obj[base + "_zh"] || obj[base + "_en"] || "";
    return obj[L.current] || obj.zh || obj.en || "";
  };

  L.set = function (lang) {
    L.current = lang === "en" ? "en" : "zh";
    document.documentElement.lang = L.current === "zh" ? "zh-CN" : "en";
    KB.util.store.set("lang", L.current);
    L.apply(document);
  };

  // data-i18n → textContent, data-i18n-aria → aria-label, data-i18n-placeholder → placeholder
  L.apply = function (root) {
    var r = root || document;
    Array.prototype.forEach.call(r.querySelectorAll("[data-i18n]"), function (n) {
      n.textContent = L.t(n.getAttribute("data-i18n"));
    });
    Array.prototype.forEach.call(r.querySelectorAll("[data-i18n-aria]"), function (n) {
      n.setAttribute("aria-label", L.t(n.getAttribute("data-i18n-aria")));
    });
    Array.prototype.forEach.call(r.querySelectorAll("[data-i18n-placeholder]"), function (n) {
      n.setAttribute("placeholder", L.t(n.getAttribute("data-i18n-placeholder")));
    });
    document.title = L.t("doc_title");
  };
})();
