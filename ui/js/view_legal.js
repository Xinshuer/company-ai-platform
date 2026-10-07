// Contract review (M2, app legal_review, route #/legal; #/legal/<review_id> opens a saved review).
// Four tabs on one app: review (stored contract, pasted text or an uploaded file), draft, contracts
// due, saved reviews. Every run goes through POST /api/runs with a JSON request naming the mode
// (corpai/agents/legal/_input.py); uploads and lists use /api/legal/* (corpai/api/routes_legal.py).
//
// Contract text, quotes, explanations and redlines are counterparty or model text: they reach the
// page only as text nodes (KB.util.el), highlights are <mark> elements around text slices, never
// markup. Personas without the app get the explicit denial (KB.appPage.denied).
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  var APP = "legal_review";
  var MAX_UPLOAD = 200000;   // corpai/agents/legal/uploads.py MAX_CHARS
  var INLINE_MAX = 3000;     // longer pasted text is uploaded first: a run's input holds 4000 characters
  // Draft templates and their variables, as in corpai/legal/draft.py TEMPLATES (tests/m2 checks this)
  var TEMPLATES = {
    "MSA": ["customer_name", "customer_city", "fleet_size", "effective_on", "term_months"],
    "order_form": ["customer_name", "customer_city", "fleet_size", "effective_on", "term_months"],
    "NDA": ["customer_name", "customer_city", "effective_on", "term_months"]
  };
  var TEMPLATE_KEYS = {"MSA": "legal_tpl_msa", "order_form": "legal_tpl_order_form", "NDA": "legal_tpl_nda"};
  var INT_VARS = {fleet_size: true, term_months: true};
  var TABS = [["review", "legal_tab_review"], ["draft", "legal_tab_draft"], ["due", "legal_tab_due"],
              ["reviews", "legal_tab_reviews"]];
  var MARK_QUOTE = "background: var(--l3-bg); color: var(--text); border-radius: 3px;";
  var MARK_FINDING = "background: var(--bad-bg); color: var(--bad); border-radius: 3px; font-weight: 600;";

  // ------------------------------------------------------------------ requests

  function startRun(request) {
    return KB.api.post("/api/runs", {app: APP, input: JSON.stringify(request), thread_id: null, user_level: 1});
  }

  function fail(out, e) {
    if (e && e.status === 401) return KB.app.sessionLost();
    U.clear(out).appendChild(C.errorBox(e));
  }

  // Demo mode only: the recorded requests of this persona, to pre-fill the forms (js/demo_legal.js).
  function samples() {
    if (KB.api.mode !== "demo") return Promise.resolve([]);
    return KB.api.get("/api/legal/samples").then(function (r) { return (r && r.requests) || []; },
                                                 function () { return []; });
  }

  function field(id, labelKey, control, hint) {
    return el("div", {class: "filter grow"}, [el("label", {for: id, class: "label"}, t(labelKey)), control,
      hint ? el("p", {class: "small muted"}, hint) : null]);
  }

  // ------------------------------------------------------------------ small renderers

  function riskChip(r) {
    var k = r === "low" || r === "medium" ? r : "high";   // unknown reads as the strictest
    return el("span", {class: "chip sev sev-" + k}, t("legal_risk_" + k));
  }

  function verdictChip(v) {
    var cls = v === "acceptable" ? "ok" : (v === "fallback" ? "wait" : "bad");
    var key = "legal_verdict_" + v;
    return el("span", {class: "chip status status-" + cls}, KB.lang.has(key) ? t(key) : String(v));
  }

  function posLabel(p) {
    var key = "legal_pos_" + p;
    return KB.lang.has(key) ? t(key) : String(p || "");
  }

  function kv(rows) {
    return el("dl", {class: "kv kv-inline"}, rows.reduce(function (acc, r) {
      if (r[1] === null || r[1] === undefined || r[1] === "") return acc;
      acc.push(el("dt", {}, t(r[0])));
      acc.push(el("dd", {}, r[1]));
      return acc;
    }, []));
  }

  function table(headers, rows) {
    return el("div", {class: "table-wrap"}, [el("table", {class: "table"}, [
      el("thead", {}, [el("tr", {}, headers.map(function (h) { return el("th", {scope: "col"}, t(h)); }))]),
      el("tbody", {}, rows.map(function (r) {
        return el("tr", {}, r.map(function (cell) { return el("td", {}, cell); }));
      }))
    ])]);
  }

  function quoteBlock(q, style) {
    return el("blockquote", {class: "small"}, [el("mark", {style: style}, q.text),
      el("span", {class: "muted"}, " (" + t("legal_quote_at", {start: q.start, end: q.end}) + ")")]);
  }

  // The reviewed text with verified quotes and AI-directed passages marked. Offsets index into the
  // reviewed text (corpai.legal quotes); a range whose slice does not equal its quote is skipped.
  function highlighted(text, review) {
    var n = text.length;
    var kind = new Array(n);
    function mark(q, k) {
      if (!q || typeof q.start !== "number" || typeof q.end !== "number") return;
      if (q.start < 0 || q.end > n || q.start >= q.end || text.slice(q.start, q.end) !== q.text) return;
      for (var i = q.start; i < q.end; i++) if (kind[i] !== 2) kind[i] = k;
    }
    (review.clauses || []).forEach(function (c) { (c.quotes || []).forEach(function (q) { mark(q, 1); }); });
    (review.findings || []).forEach(function (f) { mark(f.quote, 2); });
    var parts = [];
    var start = 0;
    for (var i = 1; i <= n; i++) {
      if (i === n || kind[i] !== kind[start]) {
        var s = text.slice(start, i);
        if (kind[start] === 1) parts.push(el("mark", {style: MARK_QUOTE}, s));
        else if (kind[start] === 2) parts.push(el("mark", {style: MARK_FINDING}, s));
        else parts.push(s);
        start = i;
      }
    }
    return el("pre", {class: "span-text", style: "max-height: 420px; overflow: auto;", tabindex: "0"}, parts);
  }

  // ------------------------------------------------------------------ review result

  function findingsBanner(findings) {
    var items = findings.map(function (f) {
      var where = f.clause_id ? t("legal_finding_clause", {id: f.clause_id}) : t("legal_finding_contract");
      var by = f.detected_by === "code+model" ? "legal_detected_both" :
        (f.detected_by === "model" ? "legal_detected_model" : "legal_detected_code");
      return el("li", {}, [
        el("p", {}, [el("strong", {}, where), " · ", t("legal_detected_by"), ": ", t(by), " · ",
          el("span", {class: "chip status status-ok"}, t("legal_effect_none"))]),
        f.quote ? quoteBlock(f.quote, MARK_FINDING) : null
      ]);
    });
    return C.panel("bad", "shield", t("legal_findings_title"), [
      el("p", {}, t("legal_findings_text")),
      el("ul", {}, items)
    ]);
  }

  function clauseCard(c) {
    var title = (c.clause_id ? t("legal_finding_clause", {id: c.clause_id}) + " · " : "") + (c.heading || c.topic || "");
    var body = [
      el("div", {class: "run-head"}, [el("strong", {}, title), verdictChip(c.verdict), riskChip(c.risk),
        el("code", {class: "mono small", title: t("legal_col_rule")}, c.rule || "—"),
        c.embedded_instruction ? el("span", {class: "chip sev sev-high"}, t("legal_embedded_badge")) : null]),
      c.explanation ? el("p", {}, c.explanation) : null
    ];
    if (c.error) body.push(el("p", {class: "small"}, t("legal_clause_error", {e: c.error})));
    if ((c.quotes || []).length) {
      body.push(el("h3", {class: "label"}, t("legal_quotes")));
      c.quotes.forEach(function (q) { body.push(quoteBlock(q, c.embedded_instruction ? MARK_FINDING : MARK_QUOTE)); });
    } else if (c.clause_id) {
      body.push(el("p", {class: "small muted"}, t("legal_no_quotes")));
    }
    if (c.suggestion) {
      body.push(el("h3", {class: "label"}, [t("legal_suggestion"), " ", C.mono(c.suggestion.clause_id, "small"),
        " · ", posLabel(c.suggestion.position)]));
      body.push(el("p", {class: "span-text"}, c.suggestion.text));
    } else if (c.suggestion_note) {
      body.push(el("p", {class: "small muted"}, c.suggestion_note));
    }
    if (c.redline && c.redline.replacement) {
      body.push(el("details", {class: "raw"}, [el("summary", {}, t("legal_redline")),
        el("p", {class: "span-text"}, c.redline.replacement)]));
    }
    return el("article", {class: "card pad"}, body);
  }

  // "Send to the counterparty": a separate run that files legal.send_contract (tier 3). Its request
  // carries the contract id, the recipient and the lawyer's own note, never anything from a review.
  function sendForm(contractId, prefill) {
    var out = el("div", {class: "result-slot", "aria-live": "polite"});
    var box = el("section", {class: "card pad"}, [el("h2", {class: "card-title"}, [C.icon("send", "icon-sm"),
      t("legal_send_title")])]);
    box.appendChild(C.panel("wait", "lock", C.tierBadge(3), [el("p", {}, t("legal_send_explain"))]));
    if (!contractId) {
      box.appendChild(el("p", {class: "small muted"}, t("legal_send_no_contract")));
      return box;
    }
    var toId = U.uid("legal-to");
    var noteId = U.uid("legal-note");
    var to = el("input", {id: toId, class: "input", type: "email", maxlength: "200", autocomplete: "off",
                          value: (prefill && prefill.to) || ""});
    var note = el("textarea", {id: noteId, class: "input", rows: "3", maxlength: "2000"});
    note.value = (prefill && prefill.note) || "";
    var btn = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("inbox", "icon-sm"), t("legal_send_button")]);
    var form = el("form", {class: "filters", novalidate: true}, [
      el("div", {class: "filter"}, [el("span", {class: "label"}, t("legal_send_contract")), C.mono(contractId)]),
      field(toId, "legal_send_to", to),
      field(noteId, "legal_send_note", note),
      el("div", {class: "filter end"}, [btn])
    ]);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var addr = to.value.trim();
      if (!addr) { to.setAttribute("aria-invalid", "true"); to.focus(); U.toast(t("legal_send_need"), "bad"); return; }
      to.removeAttribute("aria-invalid");
      btn.disabled = true;
      U.clear(out).appendChild(C.loading(t("running")));
      startRun({mode: "send", contract_id: contractId, to: addr, note: note.value.trim()}).then(function (run) {
        renderRun(out, run, contractId);
      }).catch(function (e) { fail(out, e); }).then(function () { btn.disabled = false; });
    });
    box.appendChild(form);
    box.appendChild(out);
    return box;
  }

  function reviewView(review, doc, sendPrefill) {
    var s = review.summary || {};
    var counts = s.counts || {};
    var wrap = el("div", {class: "result-slot"});
    var calls = parseInt(s.model_calls, 10) || 0;
    wrap.appendChild(el("section", {class: "card pad"}, [
      el("div", {class: "run-head"}, [el("strong", {}, t("legal_overall_risk")), riskChip(s.overall_risk),
        C.levelChip(review.level, {compact: true}), C.routeBadge(review.route), el("span", {class: "spacer"}),
        C.mono(review.review_id || "—", "small")]),
      el("p", {}, t("legal_counts", {acceptable: counts.acceptable || 0, fallback: counts.fallback || 0,
                                       unacceptable: counts.unacceptable || 0, missing: counts.missing || 0})),
      kv([
        ["legal_col_contract", review.contract_id ? C.mono(review.contract_id) : null],
        ["legal_source", doc && doc.source ? t("legal_source_" + doc.source) + (doc.filename ? " · " + doc.filename : "") : null],
        ["legal_playbook", C.mono(review.playbook_ref || "—", "small")],
        ["legal_review_id", review.stored ? t("legal_stored") : t("legal_not_stored")],
        ["legal_level", "L" + (review.level || 4)],
        ["evt_route", t("legal_model_calls", {n: calls, routes: (s.routes || []).join(", ") || "—"})]
      ])
    ]));
    if (s.matches_stored_text === false) {
      wrap.appendChild(C.panel("wait", "alert", t("legal_markup_warning"), null));
    }
    if ((review.findings || []).length) wrap.appendChild(findingsBanner(review.findings));
    wrap.appendChild(el("h2", {class: "section-title"}, t("legal_clauses_title")));
    var order = {unacceptable: 0, missing: 1, fallback: 2, acceptable: 3};
    function rank(c) { return order[c.verdict] === undefined ? 0 : order[c.verdict]; }
    (review.clauses || []).slice().sort(function (a, b) { return rank(a) - rank(b); })
      .forEach(function (c) { wrap.appendChild(clauseCard(c)); });
    if (s.unverified_quotes) wrap.appendChild(el("p", {class: "small muted"}, t("legal_unverified", {n: s.unverified_quotes})));
    if (doc && doc.text) {
      wrap.appendChild(el("details", {class: "raw"}, [el("summary", {}, t("legal_document")),
        highlighted(doc.text, review)]));
    } else if (doc && doc.text_withheld) {
      wrap.appendChild(el("p", {class: "small muted"}, [C.icon("eyeoff", "icon-xs"), t("legal_document_withheld")]));
    }
    wrap.appendChild(sendForm(review.contract_id, sendPrefill));
    return wrap;
  }

  // ------------------------------------------------------------------ other results

  function dueView(data) {
    var rows = data.contracts || [];
    var box = el("section", {class: "card pad"}, [
      el("h2", {class: "card-title"}, t("legal_due_title", {month: data.month})),
      el("p", {class: "small muted"}, [t("legal_today", {d: data.today}), " · ", t("legal_no_model")])
    ]);
    if (!rows.length) {
      box.appendChild(el("p", {}, t("legal_due_none", {month: data.month})));
      return box;
    }
    box.appendChild(table(["legal_col_contract", "legal_col_counterparty", "legal_col_ctype", "legal_col_expires",
                           "legal_col_notice", "legal_col_deadline", "legal_col_days", "legal_col_auto", "legal_col_action"],
      rows.map(function (r) {
        var days = r.notice_deadline_passed ? el("span", {class: "chip status status-bad"}, t("legal_days_passed"))
          : el("span", {class: "chip status status-" + (r.days_to_notice_deadline <= 30 ? "wait" : "ok")},
               t("legal_days_n", {n: r.days_to_notice_deadline}));
        return [C.mono(r.contract_id, "nowrap"), r.counterparty, r.contract_type, el("span", {class: "nowrap"}, r.expires_on),
                t("legal_days_n", {n: r.notice_days}), el("strong", {class: "nowrap"}, r.notice_deadline), days, t(r.auto_renew ? "legal_yes" : "legal_no"), r.action];
      })));
    return box;
  }

  function proposalNote(p) {
    if (!p || !p.proposal_id) return null;
    return el("p", {}, [t("legal_proposal_filed", {id: p.proposal_id, tier: t("tier_" + (p.tier === 2 ? 2 : 3))}), " ",
      el("a", {href: "#/approvals", class: "link-btn"}, [t("open_approvals"), C.icon("chevron", "icon-xs")])]);
  }

  function draftView(data) {
    var d = data.draft || {};
    var box = el("section", {class: "card pad"}, [el("h2", {class: "card-title"}, d.title || t("legal_draft_text")),
      el("p", {class: "small muted"}, t("legal_draft_note"))]);
    if ((d.missing || []).length) box.appendChild(C.panel("wait", "alert", t("legal_draft_missing", {list: d.missing.join(", ")}), null));
    if ((d.filled_by_model || []).length) box.appendChild(el("p", {class: "small"}, t("legal_draft_filled", {list: d.filled_by_model.join(", ")})));
    if (d.model_output_rejected) box.appendChild(el("p", {class: "small"}, t("legal_draft_rejected", {why: d.model_output_rejected})));
    if (data.result && data.result.contract_id) box.appendChild(el("p", {}, t("legal_draft_done", {id: data.result.contract_id})));
    else box.appendChild(proposalNote(data.proposal));
    box.appendChild(el("h3", {class: "label"}, t("legal_draft_clauses")));
    box.appendChild(table(["legal_col_section", "legal_col_type", "legal_col_library", "legal_col_position"],
      (d.clauses || []).map(function (c) {
        return [c.section, c.clause_type || "—", c.clause_id ? C.mono(c.clause_id) : "—", posLabel(c.position)];
      })));
    box.appendChild(el("h3", {class: "label"}, [t("legal_draft_text"), " ", C.mono(d.playbook_ref || "", "small")]));
    box.appendChild(el("pre", {class: "span-text", style: "max-height: 420px; overflow: auto;", tabindex: "0"}, d.text || ""));
    return box;
  }

  function sendView(data) {
    var p = data.proposal || {};
    var box = el("section", {class: "card pad"}, [el("h2", {class: "card-title"}, t("legal_send_title"))]);
    if (data.executed === true && data.result) {
      box.appendChild(C.panel("info", "checkcircle", t("legal_send_done", {id: data.result.outbox_id, to: data.result.recipient}), null));
    } else if (p.status && p.status !== "pending") {
      box.appendChild(C.panel("muted", "info", t("legal_not_done", {status: p.status}), null));
    } else if (p.proposal_id) {
      box.appendChild(C.panel("wait", "lock", t("legal_send_filed", {id: p.proposal_id}), [el("p", {}, t("four_eyes_hint"))]));
      box.appendChild(proposalNote(p));
    }
    if (data.to) box.appendChild(kv([["legal_send_contract", C.mono(data.contract_id || "")], ["legal_send_to", data.to]]));
    return box;
  }

  // A run of this app: the shared §18 card (status, route, level, timeline, trace) and the result.
  function renderRun(out, run, label, opts) {
    KB.app.rememberRun(run, label);
    U.clear(out);
    var data = (run.output && run.output.data) || {};
    var mine = {review: 1, due: 1, draft: 1, send: 1, invalid: 1};
    if (!mine[data.mode]) {  // an older recording without the M2 data shape
      out.appendChild(C.runCard(run, {openTimeline: false}));
      return;
    }
    var shell = U.copy(run);
    shell.output = {text: "", citations: [], data: {}};
    out.appendChild(C.runCard(shell, {noCitations: true}));
    if (run.status === "error") return;
    if (data.error) out.appendChild(C.errorBox({code: data.error.code, serverMessage: data.error.message}));
    if (data.mode === "review" && data.review) out.appendChild(reviewView(data.review, data.document, opts && opts.sendPrefill));
    if (data.mode === "due") out.appendChild(dueView(data));
    if (data.mode === "draft" && data.draft) out.appendChild(draftView(data));
    if (data.mode === "send" && data.proposal) out.appendChild(sendView(data));
  }

  // ------------------------------------------------------------------ tabs

  function reviewTab(panel) {
    var out = el("div", {class: "result-slot", "aria-live": "polite"});
    var pickId = U.uid("legal-pick");
    var linkId = U.uid("legal-link");
    var pasteId = U.uid("legal-paste");
    var fileId = U.uid("legal-file");
    var source = "stored";
    var fileText = null;
    var fileName = "";
    var sendPrefill = null;
    var pick = el("select", {id: pickId, class: "input"}, [el("option", {value: ""}, t("legal_pick_loading"))]);
    var link = el("select", {id: linkId, class: "input"}, [el("option", {value: ""}, t("legal_pick_none"))]);
    var paste = el("textarea", {id: pasteId, class: "input", rows: "10", placeholder: t("legal_paste_placeholder")});
    var file = el("input", {id: fileId, class: "input", type: "file", accept: ".txt,.md,text/plain"});
    var fileInfo = el("p", {class: "small muted", "aria-live": "polite"});
    var btn = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("scale", "icon-sm"), t("legal_run_review")]);
    var areas = {
      stored: field(pickId, "legal_pick_contract", pick),
      pasted: el("div", {class: "filter grow"}, [field(pasteId, "legal_source_pasted", paste), field(linkId, "legal_link_contract", link)]),
      upload: el("div", {class: "filter grow"}, [field(fileId, "legal_file_label", file), fileInfo])
    };
    var radios = {};
    var group = el("div", {class: "form-row", role: "radiogroup", "aria-label": t("legal_source_label")},
      ["stored", "pasted", "upload"].map(function (k) {
        var id = U.uid("legal-src");
        var r = el("input", {type: "radio", name: "legal-src", id: id, value: k, checked: k === source});
        r.addEventListener("change", function () { source = k; show(); });
        radios[k] = r;
        return el("label", {for: id, class: "chip-btn"}, [r, " ", t("legal_source_" + k)]);
      }));
    function show() {
      Object.keys(areas).forEach(function (k) { areas[k].hidden = k !== source; });
    }
    file.addEventListener("change", function () {
      fileText = null;
      fileName = "";
      var f = file.files && file.files[0];
      if (!f) { fileInfo.textContent = ""; return; }
      var reader = new window.FileReader();
      reader.onload = function () {
        fileText = String(reader.result || "");
        fileName = f.name || "";
        fileInfo.textContent = t("legal_file_chosen", {name: fileName, n: fileText.length});
      };
      reader.onerror = function () { fileInfo.textContent = t("legal_file_read_error"); };
      reader.readAsText(f);
    });
    var sampleRow = el("div", {class: "suggestions"});
    var form = el("form", {class: "card pad", novalidate: true}, [
      el("p", {class: "label"}, t("legal_source_label")), group,
      el("div", {class: "filters"}, [areas.stored, areas.pasted, areas.upload, el("div", {class: "filter end"}, [btn])]),
      el("p", {class: "small muted"}, [C.icon("shield", "icon-xs"), t("legal_untrusted_note")]),
      sampleRow
    ]);
    show();

    KB.api.get("/api/legal/contracts").then(function (res) {
      var list = (res && res.contracts) || [];
      U.clear(pick);
      list.forEach(function (c) {
        var label = t("legal_contract_option", {id: c.contract_id, party: c.counterparty, type: c.contract_type, status: c.status});
        // demo: a contract whose review has no recording is marked and cannot be picked
        var recorded = KB.appPage.recorded(APP, JSON.stringify({mode: "review", contract_id: c.contract_id}));
        pick.appendChild(el("option", {value: c.contract_id, disabled: !recorded, "data-unrecorded": recorded ? null : "1"},
          recorded ? label : label + t("demo_unrecorded_suffix")));
        link.appendChild(el("option", {value: c.contract_id}, label));
      });
      // select a contract that can be reviewed, never a disabled one
      var first = Array.prototype.filter.call(pick.options, function (o) { return !o.disabled; })[0];
      if (first) pick.value = first.value;
    }).catch(function (e) { U.clear(pick); fail(out, e); });

    samples().then(function (reqs) {
      reqs.forEach(function (r) {
        if (r.mode === "send") sendPrefill = r;
        if (r.mode !== "review" || !r.contract_id) return;
        var b = el("button", {class: "chip-btn", type: "button"}, [t("legal_sample_use"), " ", r.contract_id]);
        b.addEventListener("click", function () {
          pick.value = r.contract_id;
          source = "stored";
          radios.stored.checked = true;
          show();
        });
        sampleRow.appendChild(b);
      });
      if (sampleRow.firstChild) sampleRow.insertBefore(el("span", {class: "label"}, t("legal_samples")), sampleRow.firstChild);
    });

    function go(request) {
      return startRun(request).then(function (run) {
        var prefill = sendPrefill && sendPrefill.contract_id === request.contract_id ? sendPrefill : null;
        renderRun(out, run, request.contract_id || fileName || "contract", {sendPrefill: prefill});
      });
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var request = {mode: "review"};
      var text = null;
      if (source === "stored") {
        if (!pick.value) { U.toast(t("legal_need_source"), "bad"); return; }
        request.contract_id = pick.value;
      } else if (source === "pasted") {
        text = paste.value.trim();
        if (link.value) request.contract_id = link.value;
      } else {
        text = fileText;
      }
      if (source !== "stored") {
        if (!text) { U.toast(t("legal_need_source"), "bad"); return; }
        if (text.length > MAX_UPLOAD) { U.toast(t("legal_file_too_big", {n: MAX_UPLOAD}), "bad"); return; }
      }
      btn.disabled = true;
      var p;
      if (text !== null && (source === "upload" || text.length > INLINE_MAX)) {
        U.clear(out).appendChild(C.loading(t("legal_uploading")));
        p = KB.api.post("/api/legal/uploads", {text: text, filename: source === "upload" ? fileName : "pasted.txt"})
          .then(function (up) {
            request.upload_id = up.upload_id;
            U.clear(out).appendChild(C.loading(t("running")));
            return go(request);
          });
      } else {
        if (text !== null) request.text = text;
        U.clear(out).appendChild(C.loading(t("running")));
        p = go(request);
      }
      p.catch(function (e) { fail(out, e); }).then(function () { btn.disabled = false; });
    });
    panel.appendChild(form);
    panel.appendChild(out);
  }

  function draftTab(panel) {
    var out = el("div", {class: "result-slot", "aria-live": "polite"});
    var tplId = U.uid("legal-tpl");
    var accId = U.uid("legal-acc");
    var insId = U.uid("legal-ins");
    var tpl = el("select", {id: tplId, class: "input"}, Object.keys(TEMPLATES).map(function (k) {
      return el("option", {value: k}, t(TEMPLATE_KEYS[k]));
    }));
    var acc = el("input", {id: accId, class: "input", type: "text", maxlength: "6", placeholder: "A-0003", autocomplete: "off"});
    var ins = el("textarea", {id: insId, class: "input", rows: "3", maxlength: "2000"});
    var varBox = el("div", {class: "filters"});
    var inputs = {};
    function drawVars(values) {
      U.clear(varBox);
      inputs = {};
      TEMPLATES[tpl.value].forEach(function (name) {
        var id = U.uid("legal-var");
        var type = INT_VARS[name] ? "number" : (name === "effective_on" ? "date" : "text");
        var inp = el("input", {id: id, class: "input", type: type, maxlength: "120",
                               value: values && values[name] !== undefined ? String(values[name]) : ""});
        inputs[name] = inp;
        varBox.appendChild(field(id, "legal_var_" + name, inp));
      });
    }
    tpl.addEventListener("change", function () { drawVars(null); });
    drawVars(null);
    var btn = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("doc", "icon-sm"), t("legal_draft_button")]);
    var form = el("form", {class: "card pad", novalidate: true}, [
      el("div", {class: "filters"}, [field(tplId, "legal_draft_template", tpl), field(accId, "legal_draft_account", acc)]),
      varBox,
      el("div", {class: "filters"}, [field(insId, "legal_draft_instructions", ins), el("div", {class: "filter end"}, [btn])]),
      el("p", {class: "small muted"}, [C.icon("info", "icon-xs"), t("legal_draft_note")])
    ]);
    samples().then(function (reqs) {
      var d = reqs.filter(function (r) { return r.mode === "draft"; })[0];
      if (!d || !TEMPLATES[d.template_type]) return;
      tpl.value = d.template_type;
      drawVars(d.variables || {});
      acc.value = d.account_id || "";
      ins.value = d.instructions || "";
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var account = acc.value.trim();
      if (!account) { acc.setAttribute("aria-invalid", "true"); acc.focus(); U.toast(t("legal_need_account"), "bad"); return; }
      acc.removeAttribute("aria-invalid");
      var variables = {};
      Object.keys(inputs).forEach(function (name) {
        var v = inputs[name].value.trim();
        if (!v) return;
        variables[name] = INT_VARS[name] && /^[0-9]+$/.test(v) ? parseInt(v, 10) : v;
      });
      btn.disabled = true;
      U.clear(out).appendChild(C.loading(t("running")));
      startRun({mode: "draft", template_type: tpl.value, account_id: account, variables: variables,
                instructions: ins.value.trim()}).then(function (run) {
        renderRun(out, run, account);
      }).catch(function (e) { fail(out, e); }).then(function () { btn.disabled = false; });
    });
    panel.appendChild(form);
    panel.appendChild(out);
  }

  function dueTab(panel) {
    var out = el("div", {class: "result-slot", "aria-live": "polite"});
    var monthId = U.uid("legal-month");
    var month = el("input", {id: monthId, class: "input", type: "month", placeholder: "2026-11"});
    var btn = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("clock", "icon-sm"), t("legal_due_button")]);
    var form = el("form", {class: "card pad filters", novalidate: true}, [
      field(monthId, "legal_due_month", month, t("legal_due_hint")),
      el("div", {class: "filter end"}, [btn])
    ]);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var request = {mode: "due"};
      if (month.value) request.month = month.value;
      btn.disabled = true;
      U.clear(out).appendChild(C.loading(t("running")));
      startRun(request).then(function (run) { renderRun(out, run, t("legal_tab_due")); })
        .catch(function (e) { fail(out, e); }).then(function () { btn.disabled = false; });
    });
    panel.appendChild(form);
    panel.appendChild(out);
  }

  function reviewsTab(panel) {
    var out = el("div", {class: "result-slot"}, [C.loading()]);
    panel.appendChild(out);
    KB.api.get("/api/legal/reviews").then(function (res) {
      U.clear(out);
      var list = (res && res.reviews) || [];
      if (!list.length) {
        out.appendChild(el("div", {class: "empty-state"}, [C.icon("inbox", "icon-lg"), el("p", {}, t("legal_reviews_empty"))]));
        return;
      }
      out.appendChild(table(["legal_col_review", "legal_col_contract", "legal_overall_risk", "legal_col_verdict",
                             "legal_col_requester", "legal_col_created", "legal_level"],
        list.map(function (r) {
          var s = r.summary || {};
          var c = s.counts || {};
          return [el("a", {href: "#/legal/" + encodeURIComponent(r.review_id), class: "link-btn"}, [C.mono(r.review_id, "small")]),
                  r.contract_id ? C.mono(r.contract_id) : "—", riskChip(s.overall_risk),
                  t("legal_counts", {acceptable: c.acceptable || 0, fallback: c.fallback || 0,
                                     unacceptable: c.unacceptable || 0, missing: c.missing || 0}),
                  r.requested_by, U.fmtTime(r.created_at), C.levelChip(r.level, {compact: true})];
        })));
    }).catch(function (e) { fail(out, e); });
  }

  function reviewDetail(root, reviewId) {
    root.appendChild(el("p", {}, [el("a", {href: "#/legal", class: "link-btn",
      on: {click: function () { KB.state.legalTab = "reviews"; }}}, [t("legal_back_reviews")])]));
    var out = el("div", {class: "result-slot"}, [C.loading()]);
    root.appendChild(out);
    KB.api.get("/api/legal/reviews/" + encodeURIComponent(reviewId)).then(function (review) {
      U.clear(out);
      out.appendChild(el("h2", {class: "section-title"}, t("legal_stored_review")));
      out.appendChild(reviewView(review, null, null));
    }).catch(function (e) { fail(out, e); });
  }

  KB.views = KB.views || {};
  KB.views.legal = {
    render: function (root, args) {
      root.appendChild(C.pageHeader("h-legal", "app_legal_review", "app_legal_review_desc",
        [el("span", {title: t("legal_route_hint")}, [C.routeBadge("local")])]));
      if (!KB.appPage.hasApp(APP)) {
        KB.appPage.denied(root, APP);
        return;
      }
      root.appendChild(el("p", {class: "small muted"}, [C.icon("cpu", "icon-xs"), t("legal_route_hint")]));
      KB.appPage.demoCoverage(root, APP);  // demo: say up front when this persona has no recording
      if (args && args[0]) {
        reviewDetail(root, args[0]);
        return;
      }
      var current = KB.state.legalTab || "review";
      var panelId = U.uid("legal-panel");
      var tabs = el("div", {class: "tabs", role: "tablist", "aria-label": t("app_legal_review")});
      TABS.forEach(function (tb) {
        var tab = el("button", {class: "tab", role: "tab", type: "button", "aria-selected": tb[0] === current ? "true" : "false",
                                "aria-controls": panelId}, t(tb[1]));
        tab.addEventListener("click", function () { KB.state.legalTab = tb[0]; KB.router.refresh(); });
        tabs.appendChild(tab);
      });
      var panel = el("div", {class: "tab-panel", role: "tabpanel", id: panelId});
      root.appendChild(tabs);
      root.appendChild(panel);
      if (current === "draft") draftTab(panel);
      else if (current === "due") dueTab(panel);
      else if (current === "reviews") reviewsTab(panel);
      else reviewTab(panel);
    }
  };
})();
