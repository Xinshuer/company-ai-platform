// Persona picker: the 14 personas grouped by department, with role badges and clearance.
// In live mode this is the demo login (POST /api/auth/demo-login), which the server audits and
// only offers when CORPAI_DEMO_LOGIN=1.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var el = KB.util.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var DEPT_ORDER = ["exec", "sales", "eng", "legal", "finance", "hr", "it"];
  // clearance per §5.1, shown on the card so the picker already explains why answers differ
  function clearanceOf(p) {
    if (p.role === "ceo" || p.role === "manager") return 4;
    if (p.role === "platform_admin" || p.role === "security_auditor") return 2;
    if (p.dept === "legal" || p.dept === "finance" || p.dept === "hr") return 4;
    return 3;
  }

  function personaButton(p, onPick) {
    var b = el("button", {class: "persona", type: "button", "aria-label": t("login_as", {name: p.name, title: p.title})}, [
      C.avatar(p.name, p.dept, "md"),
      el("span", {class: "persona-text"}, [
        el("span", {class: "persona-name"}, p.name),
        el("span", {class: "persona-title"}, p.title),
        el("span", {class: "persona-tags"}, [C.roleBadge(p.role), C.levelChip(clearanceOf(p), {compact: true, prefix: t("clearance_short")})])
      ]),
      C.icon("chevron", "persona-go")
    ]);
    b.addEventListener("click", function () { onPick(p, b); });
    return b;
  }

  KB.views = KB.views || {};
  KB.views.login = {
    render: function (root) {
      var list = el("div", {class: "dept-grid"}, [C.loading()]);
      var msg = el("div", {"aria-live": "polite"});
      root.appendChild(C.pageHeader("h-login", "login_title", "login_sub"));
      root.appendChild(C.panel("info", "info", null, [
        el("p", {}, t(KB.api.mode === "live" ? "login_note_live" : "login_note_demo"))
      ]));
      root.appendChild(msg);
      root.appendChild(list);

      function pick(p, btn) {
        btn.disabled = true;
        btn.classList.add("busy");
        KB.app.login(p.user_id).catch(function (e) {
          btn.disabled = false;
          btn.classList.remove("busy");
          KB.util.clear(msg).appendChild(C.errorBox(e));
        });
      }

      KB.api.get("/api/personas").then(function (personas) {
        KB.util.clear(list);
        var groups = {};
        (personas || []).forEach(function (p) { (groups[p.dept] = groups[p.dept] || []).push(p); });
        var depts = DEPT_ORDER.filter(function (d) { return groups[d]; })
          .concat(Object.keys(groups).filter(function (d) { return DEPT_ORDER.indexOf(d) < 0; }));
        depts.forEach(function (d) {
          list.appendChild(el("section", {class: "dept-card dept-" + d, "aria-label": t("dept_" + d)}, [
            el("h2", {class: "dept-title"}, [el("span", {class: "dept-dot", "aria-hidden": "true"}), t("dept_" + d),
              el("span", {class: "dept-count"}, String(groups[d].length))]),
            el("div", {class: "persona-list"}, groups[d].map(function (p) { return personaButton(p, pick); }))
          ]));
        });
      }).catch(function (e) {
        KB.util.clear(list).appendChild(C.errorBox(e));
      });
    }
  };
})();
