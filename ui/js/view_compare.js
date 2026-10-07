// "同一问题，不同的人": the same question asked by three personas, side by side.
// Differences come only from each person's rights (RLS, tool rules, clearance), never from
// the prompt: the route and the high-water mark follow from what each person could read.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  function fixtures() { return window.FIXTURES || {}; }  // loaded in demo mode only

  function column(p, res, uniqueIds) {
    var head = el("div", {class: "cmp-head"}, [
      C.avatar(p.name, p.dept, "md"),
      el("div", {}, [el("strong", {}, p.name), el("div", {class: "small muted"}, p.title),
        el("div", {class: "cmp-tags"}, [C.roleBadge(p.role)])])
    ]);
    var body = [];
    if (res.error) {
      body.push(C.errorBox(res.error));
    } else {
      var run = res.run;
      body.push(el("div", {class: "run-head"}, [C.statusChip(run.status), C.routeBadge(run.route), C.hwmChip(run.hwm)]));
      if (run.status === "error" && run.error) body.push(C.errorBox({code: run.error.code, serverMessage: run.error.message}));
      if (run.output && run.output.text) body.push(el("div", {class: "answer"}, U.linkify(run.output.text)));
      var denied = C.deniedTools(run.events);
      if (denied.length) {
        body.push(el("div", {class: "cmp-denied"}, [el("h3", {class: "label"}, [C.icon("lock", "icon-xs"), t("cmp_denied")]),
          el("ul", {}, denied.map(function (d) { return el("li", {}, C.mono(d.domain + "." + d.tool, "small")); }))]));
      }
      var cits = (run.output && run.output.citations) || [];
      body.push(C.citations(cits));
      var only = cits.filter(function (c) { return uniqueIds[c.doc_id]; });
      if (only.length) {
        body.push(el("p", {class: "small"}, [el("span", {class: "pill pill-strong"}, t("cmp_only_here", {n: only.length}))]));
      }
      body.push(C.timeline(run.events));
      // a trace is readable by its owner only: no link in the other people's columns
      var mine = KB.state.me && p.user_id === KB.state.me.user_id;
      if (mine && C.canTrace(run)) {
        body.push(el("p", {}, [el("a", {class: "link-btn small", href: "#/trace/" + encodeURIComponent(run.run_id)},
          [C.icon("activity", "icon-xs"), t("view_trace")])]));
      }
    }
    return el("article", {class: "card cmp-col"}, [head, el("div", {class: "cmp-body"}, body)]);
  }

  function summary(people, results) {
    return el("div", {class: "table-wrap"}, [el("table", {class: "table"}, [
      el("caption", {class: "sr-only"}, t("cmp_summary")),
      el("thead", {}, [el("tr", {}, ["cmp_person", "col_route", "hwm_short", "cmp_citations", "cmp_denied"].map(function (k) {
        return el("th", {scope: "col"}, t(k));
      }))]),
      el("tbody", {}, people.map(function (p, i) {
        var r = results[i];
        if (r.error) return el("tr", {}, [el("th", {scope: "row"}, p.name), el("td", {colspan: "4"}, KB.lang.has("err_" + r.error.code) ? t("err_" + r.error.code) : t("err_unknown"))]);
        var run = r.run;
        return el("tr", {}, [
          el("th", {scope: "row"}, p.name),
          el("td", {}, C.routeBadge(run.route)),
          el("td", {}, C.levelChip(run.hwm, {compact: true})),
          el("td", {}, String(((run.output && run.output.citations) || []).length)),
          el("td", {}, String(C.deniedTools(run.events).length))
        ]);
      }))
    ])]);
  }

  // Live mode: log in as each persona in turn (demo login, audited), then back as yourself.
  function liveRun(people, body) {
    var me = KB.state.me;
    var results = [];
    var chain = Promise.resolve();
    people.forEach(function (p) {
      chain = chain.then(function () {
        return KB.api.post("/api/auth/demo-login", {user_id: p.user_id})
          .then(function () { return KB.api.post("/api/runs", body); })
          .then(function (run) { results.push({run: run}); }, function (e) { results.push({error: e}); });
      });
    });
    return chain.then(function () {
      return KB.api.post("/api/auth/demo-login", {user_id: me.user_id}).then(function () { return results; },
        function () {
          // Logging back in failed: the session may belong to the last persona now. Re-read who
          // is logged in so the header never shows the wrong identity, and say so.
          return KB.app.loadMe().then(function (now) {
            if (!now) { KB.app.sessionLost(); return results; }
            if (now.user_id !== me.user_id) U.toast(t("cmp_relogin_failed", {name: now.name}), "bad");
            return results;
          });
        });
    });
  }

  KB.views = KB.views || {};
  KB.views.compare = {
    render: function (root) {
      var cfg = fixtures().compare || {question: "", app: "qa", personas: ["sales_staff", "eng_staff", "ceo"]};
      root.appendChild(C.pageHeader("h-compare", "nav_compare", "compare_sub"));
      root.appendChild(C.panel("info", "info", null, [el("p", {}, t(KB.api.mode === "live" ? "compare_live_note" : "compare_demo_note"))]));

      var qId = U.uid("cmp-q");
      var q = el("input", {id: qId, class: "input", type: "text", maxlength: "500", value: cfg.question || ""});
      var selects = [0, 1, 2].map(function (i) {
        return el("select", {id: "cmp-p" + i, class: "input input-sm", "aria-label": t("cmp_person_n", {n: i + 1})});
      });
      var go = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("columns", "icon-sm"), t("cmp_run")]);
      var form = el("form", {class: "card pad", novalidate: true}, [
        el("label", {for: qId, class: "label"}, t("cmp_question")),
        q,
        el("div", {class: "cmp-pick"}, selects.map(function (s, i) {
          return el("div", {class: "filter"}, [el("label", {for: "cmp-p" + i, class: "small"}, t("cmp_person_n", {n: i + 1})), s]);
        }).concat([el("div", {class: "filter end"}, [go])]))
      ]);
      var out = el("div", {class: "cmp-out", "aria-live": "polite"});
      root.appendChild(form);
      root.appendChild(out);

      var personas = [];
      KB.api.get("/api/personas").then(function (list) {
        personas = list || [];
        selects.forEach(function (s, i) {
          personas.forEach(function (p) {
            s.appendChild(el("option", {value: p.persona_key}, p.name + " · " + p.title));
          });
          s.value = (cfg.personas || [])[i] || (personas[i] && personas[i].persona_key) || "";
        });
      }).catch(function (e) {
        U.clear(out).appendChild(C.errorBox(e));
      });

      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var question = q.value.trim();
        if (!question) { q.focus(); return; }
        var people = selects.map(function (s) {
          return personas.filter(function (p) { return p.persona_key === s.value; })[0];
        }).filter(Boolean);
        if (!people.length) return;
        var body = {app: cfg.app || "qa", input: question, thread_id: null, user_level: 1};
        go.disabled = true;
        U.clear(out).appendChild(C.loading(t("cmp_running")));
        var p = KB.api.mode === "live" ? liveRun(people, body) : Promise.all(people.map(function (pp) {
          return KB.demo.runAs(pp.persona_key, body).then(function (run) { return {run: run}; }, function (e) { return {error: e}; });
        }));
        p.then(function (results) {
          // a citation seen by exactly one person shows what the others could not read
          var count = {};
          results.forEach(function (r) {
            var seen = {};
            ((r.run && r.run.output && r.run.output.citations) || []).forEach(function (c) {
              if (!seen[c.doc_id]) { count[c.doc_id] = (count[c.doc_id] || 0) + 1; seen[c.doc_id] = true; }
            });
          });
          var unique = {};
          Object.keys(count).forEach(function (k) { if (count[k] === 1) unique[k] = true; });
          U.clear(out);
          out.appendChild(el("h2", {class: "section-title"}, t("cmp_summary")));
          out.appendChild(summary(people, results));
          out.appendChild(el("div", {class: "cmp-grid"}, people.map(function (pp, i) { return column(pp, results[i], unique); })));
        }).catch(function (e) {
          U.clear(out).appendChild(C.errorBox(e));
        }).then(function () { go.disabled = false; });
      });
    }
  };
})();
