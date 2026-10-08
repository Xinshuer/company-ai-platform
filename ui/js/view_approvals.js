// Approvals inbox. A human approves exactly what will run: every parameter is shown in full,
// and the params_hash the approver saw is sent back, so the executor can refuse anything that
// changed in between (ENGINEERING.md rule 7). Tier 3 is four-eyes: the requester's own buttons
// are disabled and the server's why_not text says why.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  function valueCell(v) {
    if (v === null || v === undefined) return el("span", {class: "muted"}, "null");
    if (typeof v === "string") return el("span", {class: "param-str"}, v);
    if (typeof v === "number" || typeof v === "boolean") return C.mono(String(v));
    return el("pre", {class: "json json-inline"}, U.json(v));
  }

  function paramsTable(params) {
    var keys = Object.keys(params || {});
    if (!keys.length) return el("p", {class: "muted small"}, t("params_empty"));
    return el("div", {class: "table-wrap"}, [el("table", {class: "table params"}, [
      el("caption", {class: "sr-only"}, t("params_title")),
      el("thead", {}, [el("tr", {}, [el("th", {scope: "col"}, t("col_param")), el("th", {scope: "col"}, t("col_value"))])]),
      el("tbody", {}, keys.map(function (k) {
        return el("tr", {}, [el("th", {scope: "row"}, C.mono(k)), el("td", {}, valueCell(params[k]))]);
      }))
    ])]);
  }

  function meta(p) {
    var rows = [
      ["col_requester", p.requester ? p.requester.name + " (" + p.requester.user_id + ")" : "—"],
      ["col_agent", p.agent || "—"],
      ["col_created", U.fmtTime(p.created_at)]
    ];
    if (p.approver) rows.push(["col_approver", p.approver.name + " (" + p.approver.user_id + ")"]);
    if (p.decided_at) rows.push(["col_decided", U.fmtTime(p.decided_at)]);
    if (p.reason) rows.push(["col_reason", p.reason]);
    return el("dl", {class: "kv kv-inline"}, rows.reduce(function (acc, r) {
      acc.push(el("dt", {}, t(r[0])));
      acc.push(el("dd", {}, r[1]));
      return acc;
    }, []));
  }

  function proposalCard(p, onDone) {
    var me = KB.state.me;
    var isMine = p.requester && p.requester.user_id === me.user_id;
    var reasonId = U.uid("reason");
    var card = el("article", {class: "card proposal status-" + p.status});
    var out = el("div", {"aria-live": "polite"});

    card.appendChild(el("div", {class: "prop-head"}, [
      el("span", {class: "prop-tool"}, [C.icon("tool", "icon-sm"), C.mono(p.domain + "." + p.tool)]),
      C.tierBadge(p.tier),
      C.statusChip(p.status),
      el("span", {class: "spacer"}),
      C.mono(p.proposal_id, "small muted")
    ]));
    card.appendChild(el("p", {class: "prop-summary"}, p.summary || ""));
    card.appendChild(meta(p));

    card.appendChild(el("h3", {class: "label"}, t("params_title")));
    card.appendChild(el("p", {class: "small muted"}, t("params_hint")));
    card.appendChild(paramsTable(p.params));
    var raw = el("details", {class: "raw"}, [el("summary", {}, t("params_raw")), C.jsonBlock(p.params)]);
    card.appendChild(raw);

    card.appendChild(el("div", {class: "hash-row"}, [
      el("span", {class: "label"}, [C.icon("hash", "icon-xs"), t("params_hash")]),
      el("code", {class: "mono hash"}, p.params_hash || "—"),
      p.params_hash ? C.copyButton(p.params_hash) : null
    ]));
    card.appendChild(el("p", {class: "small muted"}, t("params_hash_hint")));

    if (p.result) {
      card.appendChild(el("h3", {class: "label"}, t("prop_result")));
      card.appendChild(C.jsonBlock(p.result));
    }

    if (p.status === "pending") {
      var allowed = !!p.can_approve;
      var ta = el("textarea", {id: reasonId, class: "input", rows: "2", maxlength: "500", required: true,
                               disabled: !allowed, placeholder: t("reason_placeholder")});
      var approve = el("button", {class: "btn btn-primary", type: "button", disabled: !allowed, "aria-disabled": allowed ? null : "true"},
        [C.icon("check", "icon-sm"), t("approve")]);
      var reject = el("button", {class: "btn btn-danger-outline", type: "button", disabled: !allowed, "aria-disabled": allowed ? null : "true"},
        [C.icon("x", "icon-sm"), t("reject")]);
      var box = el("div", {class: "decide"}, [
        el("label", {for: reasonId, class: "label"}, t("reason_label")),
        ta,
        el("div", {class: "decide-row"}, [approve, reject])
      ]);
      if (!allowed) {
        // four-eyes on your own tier-3 request has a localized explanation; the server's English
        // policy text is shown only when there is none
        var fourEyes = isMine && Number(p.tier) >= 3;
        var why = fourEyes ? [el("p", {}, t("four_eyes_hint"))]
          : [el("p", {}, [el("span", {class: "muted"}, t("server_says") + " "), p.why_not || t("why_not_default")])];
        box.insertBefore(C.panel("muted", "lock", t("cannot_decide"), why), box.firstChild);
      } else if (isMine && Number(p.tier) === 2) {
        box.insertBefore(el("p", {class: "small muted"}, t("tier2_self_hint")), box.firstChild);
      }
      card.appendChild(box);

      var act = function (isApprove) {
        var reason = ta.value.trim();
        if (!reason) {
          ta.setAttribute("aria-invalid", "true");
          ta.focus();
          U.toast(t("reason_required"), "bad");
          return;
        }
        ta.removeAttribute("aria-invalid");
        approve.disabled = reject.disabled = true;
        var path = "/api/proposals/" + encodeURIComponent(p.proposal_id) + (isApprove ? "/approve" : "/reject");
        // the hash is the one rendered above: what the approver saw is what gets executed
        var body = isApprove ? {params_hash: p.params_hash, reason: reason} : {reason: reason};
        KB.api.post(path, body).then(function (res) {
          // the decision stands either way; whether the action ran is the proposal's status
          var st = res && res.proposal && res.proposal.status;
          if (!isApprove) U.toast(t("rejected_toast"), "ok");
          else if (st === "executed") U.toast(t("approved_toast"), "ok");
          else if (st === "approved") U.toast(t("approved_pending_toast"), "ok");
          else U.toast(t("approved_failed_toast"), "bad");
          onDone(res);
        }).catch(function (e) {
          if (e.status === 401) return KB.app.sessionLost();
          approve.disabled = reject.disabled = false;
          U.clear(out).appendChild(C.errorBox(e));
        });
      };
      approve.addEventListener("click", function () { act(true); });
      reject.addEventListener("click", function () { act(false); });
    }
    card.appendChild(out);
    return card;
  }

  KB.views = KB.views || {};
  KB.views.approvals = {
    render: function (root) {
      var me = KB.state.me;
      var box = KB.state.approvalsBox || (me.can && me.can.approve ? "to_approve" : "mine");
      root.appendChild(C.pageHeader("h-approvals", "nav_approvals", "approvals_sub"));

      var tabs = C.tabs("approvals", t("nav_approvals"), [["to_approve", "box_to_approve"], ["mine", "box_mine"]], box,
                        function (k) { KB.state.approvalsBox = k; KB.router.refresh(); });
      var panel = tabs.panel;
      var resultSlot = el("div", {class: "result-slot"});
      root.appendChild(tabs.list);
      root.appendChild(resultSlot);
      root.appendChild(panel);

      if (box === "to_approve" && !(me.can && me.can.approve)) {
        panel.appendChild(C.panel("muted", "lock", t("no_approver_title"), [el("p", {}, t("no_approver_text"))]));
        return;
      }

      function showResult(res) {
        U.clear(resultSlot);
        resultSlot.appendChild(el("h2", {class: "section-title"}, t("decision_result")));
        if (res && res.proposal) resultSlot.appendChild(proposalCard(res.proposal, function () {}));
        if (res && res.run) {
          // the resumed run is the requester's: only they get its trace and keep it in their list
          var own = !!(res.proposal && res.proposal.requester && res.proposal.requester.user_id === me.user_id);
          resultSlot.appendChild(el("h3", {class: "label"}, t("resumed_run")));
          resultSlot.appendChild(C.runCard(res.run, {trace: own}));
          if (own) KB.app.rememberRun(res.run, res.proposal ? res.proposal.summary : "");
        }
        load();  // the decided proposal leaves the pending list; the result above stays
        resultSlot.scrollIntoView({block: "start"});
      }

      // one render path for the first load and every reload, empty state included
      function load() {
        U.clear(panel).appendChild(C.loading());
        KB.api.get("/api/proposals?box=" + box).then(function (list) {
          U.clear(panel);
          list = list || [];
          if (!list.length) {
            panel.appendChild(el("div", {class: "empty-state"}, [C.icon("inbox", "icon-lg"), el("p", {}, t("inbox_empty"))]));
            return;
          }
          list.forEach(function (p) { panel.appendChild(proposalCard(p, showResult)); });
        }).catch(function (e) {
          if (e.status === 401) return KB.app.sessionLost();
          U.clear(panel).appendChild(C.errorBox(e));
        });
      }
      load();
    }
  };
})();
