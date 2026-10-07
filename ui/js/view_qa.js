// Q&A chat (app "qa", milestone M1). Each answer shows the route badge, the high-water mark,
// citations with their levels and the run's event timeline. Once a thread reaches L3 the
// banner "本会话已切换到本地模式" stays for the rest of the thread: the level never goes down.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var U = KB.util;
  var el = U.el;
  var C = KB.c;
  function t(k, v) { return KB.lang.t(k, v); }

  function chat() {
    var s = KB.state;
    if (!s.chat) s.chat = {thread_id: null, hwm: 1, messages: []};
    return s.chat;
  }

  function userBubble(m) {
    return el("div", {class: "msg msg-user"}, [
      el("div", {class: "bubble"}, [
        el("p", {class: "bubble-text"}, m.text),
        m.user_level > 1 ? el("p", {class: "small"}, [t("qa_marked_as"), " ", C.levelChip(m.user_level, {compact: true})]) : null
      ]),
      C.avatar(KB.state.me.name, KB.state.me.dept, "sm")
    ]);
  }

  function botMessage(m) {
    var mark = el("span", {class: "bot-mark", "aria-hidden": "true"}, [C.icon("kestrel")]);
    if (m.run) return el("div", {class: "msg msg-bot"}, [mark, C.runCard(m.run)]);
    if (m.error) return el("div", {class: "msg msg-bot"}, [mark, el("div", {class: "card run-card"}, [C.errorBox(m.error),
      m.error.code === "demo_not_recorded" ? el("p", {class: "small muted pad"}, t("demo_try_suggestions")) : null])]);
    return el("div", {class: "msg msg-bot"}, [mark, el("div", {class: "card run-card"}, [C.loading(t("qa_thinking"))])]);
  }

  KB.views = KB.views || {};
  KB.views.qa = {
    render: function (root) {
      var st = chat();
      var newBtn = el("button", {class: "btn btn-outline btn-sm", type: "button"}, [C.icon("plus", "icon-xs"), t("qa_new_thread")]);
      root.appendChild(C.pageHeader("h-qa", "app_qa", "qa_sub", [newBtn]));

      var bannerSlot = el("div");
      var list = el("div", {class: "chat-list"});
      var live = el("p", {class: "sr-only", "aria-live": "polite", "aria-atomic": "true"});
      var sugg = el("div", {class: "suggestions"});
      var ta = el("textarea", {id: "qa-input", rows: "2", class: "input", maxlength: "4000",
                               "data-i18n-placeholder": "qa_placeholder", placeholder: t("qa_placeholder")});
      var lvlSel = el("select", {id: "qa-level", class: "input input-sm"}, [1, 2, 3, 4].map(function (n) {
        return el("option", {value: String(n)}, "L" + n + " · " + t("level_" + n));
      }));
      var send = el("button", {class: "btn btn-primary", type: "submit"}, [C.icon("send", "icon-sm"), t("qa_send")]);
      var form = el("form", {class: "composer card", novalidate: true}, [
        el("label", {for: "qa-input", class: "label"}, t("qa_input_label")),
        ta,
        el("div", {class: "composer-row"}, [
          el("label", {for: "qa-level", class: "small"}, t("qa_level_label")),
          lvlSel,
          el("span", {class: "small muted hide-sm"}, t("qa_level_hint")),
          el("span", {class: "spacer"}),
          send
        ])
      ]);

      function paintBanner() {
        U.clear(bannerSlot);
        if (st.hwm >= 3) bannerSlot.appendChild(C.localBanner());
      }

      function paint() {
        U.clear(list);
        if (!st.messages.length) {
          list.appendChild(el("div", {class: "empty-state"}, [C.icon("chat", "icon-lg"), el("p", {}, t("qa_empty"))]));
        }
        st.messages.forEach(function (m) { list.appendChild(m.role === "user" ? userBubble(m) : botMessage(m)); });
        paintBanner();
      }

      function paintSuggestions(qs) {
        U.clear(sugg);
        if (!qs.length) return;
        sugg.appendChild(el("span", {class: "small muted"}, t("qa_try")));
        qs.forEach(function (q) {
          var b = el("button", {class: "chip-btn", type: "button"}, q);
          b.addEventListener("click", function () { ta.value = q; ta.focus(); });
          sugg.appendChild(b);
        });
      }

      function submit() {
        var text = ta.value.trim();
        if (!text || send.disabled) return;
        var lvl = parseInt(lvlSel.value, 10) || 1;
        st.messages.push({role: "user", text: text, user_level: lvl});
        var pending = {role: "bot"};
        st.messages.push(pending);
        ta.value = "";
        send.disabled = true;
        paint();
        list.lastChild.scrollIntoView({block: "nearest"});
        KB.api.post("/api/runs", {app: "qa", input: text, thread_id: st.thread_id, user_level: lvl})
          .then(function (run) {
            pending.run = run;
            st.thread_id = run.thread_id || st.thread_id;
            st.hwm = Math.max(st.hwm, parseInt(run.hwm, 10) || 1);
            KB.app.rememberRun(run, text);
            live.textContent = t("qa_announce", {
              status: t("status_" + run.status), route: t("route_" + (run.route || "none")), level: "L" + run.hwm
            }) + " " + ((run.output && run.output.text) || "");
          })
          .catch(function (e) {
            if (e.status === 401) return KB.app.sessionLost();
            pending.error = e;
            live.textContent = KB.lang.has("err_" + e.code) ? t("err_" + e.code) : t("err_unknown");
          })
          .then(function () {
            send.disabled = false;
            paint();
            if (list.lastChild) list.lastChild.scrollIntoView({block: "nearest"});
          });
      }

      form.addEventListener("submit", function (ev) { ev.preventDefault(); submit(); });
      ta.addEventListener("keydown", function (ev) {
        // Enter sends, Shift+Enter breaks the line; never while an IME is composing (Chinese input)
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && ev.keyCode !== 229) {
          ev.preventDefault();
          submit();
        }
      });
      newBtn.addEventListener("click", function () {
        KB.state.chat = {thread_id: null, hwm: 1, messages: []};
        st = chat();
        paint();
        U.toast(t("qa_new_thread_done"));
        ta.focus();
      });

      root.appendChild(bannerSlot);
      root.appendChild(list);
      root.appendChild(live);
      root.appendChild(sugg);
      root.appendChild(form);
      paint();
      paintSuggestions(KB.api.mode === "demo" ? KB.demo.suggestions(KB.state.me.persona_key, "qa") : []);
    }
  };
})();
