// A small DOM for running the UI views under Node (no jsdom in this project). It supports what
// ui/js/*.js uses: elements, text nodes, attributes, classList, events, simple selectors and the
// value/selected behaviour of inputs and selects. Not a browser: layout, CSS and innerHTML are absent
// on purpose (the views never use innerHTML; a call would throw here and fail the test).
"use strict";

class Node {
  constructor(doc) { this.ownerDocument = doc; this.childNodes = []; this.parentNode = null; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    this.childNodes.push(c);
    c.parentNode = this;
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i < 0) throw new Error("not a child");
    this.childNodes.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  insertBefore(c, ref) {
    if (!ref) return this.appendChild(c);
    if (c.parentNode) c.parentNode.removeChild(c);
    const i = this.childNodes.indexOf(ref);
    this.childNodes.splice(i, 0, c);
    c.parentNode = this;
    return c;
  }
  replaceChild(c, old) { this.insertBefore(c, old); this.removeChild(old); return old; }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(""); }
  set textContent(v) {
    this.childNodes.forEach((n) => { n.parentNode = null; });
    this.childNodes = [];
    if (v !== "" && v !== null && v !== undefined) this.appendChild(this.ownerDocument.createTextNode(String(v)));
  }
}

class Text extends Node {
  constructor(doc, text) { super(doc); this.nodeType = 3; this.data = String(text); }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  get nodeValue() { return this.data; }
}

class Element extends Node {
  constructor(doc, tag) {
    super(doc);
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.attributes = {};
    this.listeners = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this._value = null;
    this.checked = false;
    this.selected = false;
    this.disabled = false;
    const self = this;
    this.classList = {
      add: (...c) => { const s = new Set(self._classes()); c.forEach((x) => s.add(x)); self.className = [...s].join(" "); },
      remove: (...c) => { self.className = self._classes().filter((x) => !c.includes(x)).join(" "); },
      contains: (c) => self._classes().includes(c),
      toggle: (c, on) => {
        const has = self._classes().includes(c);
        const want = on === undefined ? !has : !!on;
        if (want) self.classList.add(c); else self.classList.remove(c);
        return want;
      }
    };
  }
  _classes() { return String(this.className || "").split(/\s+/).filter(Boolean); }
  get className() { return this.attributes["class"] || ""; }
  set className(v) { this.attributes["class"] = String(v); }
  get id() { return this.attributes.id || ""; }
  set id(v) { this.attributes.id = String(v); }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k === "disabled") this.disabled = true;
    if (k === "selected") this.selected = true;
    if (k === "checked") this.checked = true;
  }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  hasAttribute(k) { return k in this.attributes; }
  removeAttribute(k) { delete this.attributes[k]; if (k === "disabled") this.disabled = false; }
  setAttributeNS(ns, k, v) { this.setAttribute(k, v); }
  get innerHTML() { throw new Error("innerHTML is not used by the views"); }
  set innerHTML(v) { throw new Error("innerHTML must never be assigned (data would become markup)"); }
  get options() { return this.querySelectorAll("option"); }
  get value() {
    if (this.tagName === "SELECT") {
      const opts = this.options;
      const sel = opts.filter((o) => o.selected)[0] || opts.filter((o) => !o.disabled)[0];
      return sel ? sel.value : "";
    }
    if (this._value !== null) return this._value;
    if (this.tagName === "OPTION" && !("value" in this.attributes)) return this.textContent;
    return this.attributes.value || "";
  }
  set value(v) {
    if (this.tagName === "SELECT") {
      this.options.forEach((o) => { o.selected = o.value === String(v); });
      return;
    }
    this._value = String(v);
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    (this.listeners[ev.type] || []).forEach((fn) => fn.call(this, ev));
    return !ev.defaultPrevented;
  }
  click() {
    if (this.disabled) return;
    const ev = {type: "click", defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }};
    this.dispatchEvent(ev);
    // a submit button submits its form, like a browser
    if (!ev.defaultPrevented && this.tagName === "BUTTON" && (this.getAttribute("type") || "submit") === "submit") {
      let f = this.parentNode;
      while (f && f.tagName !== "FORM") f = f.parentNode;
      if (f) f.dispatchEvent({type: "submit", defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }});
    }
  }
  focus() {}
  blur() {}
  scrollIntoView() {}
  closest(sel) {
    let n = this;
    while (n && n.nodeType === 1) { if (matches(n, sel)) return n; n = n.parentNode; }
    return null;
  }
  querySelectorAll(sel) {
    const out = [];
    const parts = String(sel).split(",").map((s) => s.trim());
    (function walk(n) {
      n.childNodes.forEach((c) => {
        if (c.nodeType === 1) {
          if (parts.some((p) => matches(c, p))) out.push(c);
          walk(c);
        }
      });
    })(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

// "tag", ".cls", "tag.cls.cls2", "#id", "[attr]", "[attr=value]", "tag[attr]"; a descendant
// selector "a b" matches b inside a.
function matches(n, sel) {
  const chain = sel.trim().split(/\s+/);
  if (chain.length > 1) {
    if (!matches(n, chain[chain.length - 1])) return false;
    let p = n.parentNode;
    const rest = chain.slice(0, -1).join(" ");
    while (p && p.nodeType === 1) { if (matches(p, rest)) return true; p = p.parentNode; }
    return false;
  }
  const m = /^([a-zA-Z0-9-]*)((?:[.#][\w-]+)*)((?:\[[^\]]+\])*)$/.exec(sel);
  if (!m) throw new Error("unsupported selector " + sel);
  if (m[1] && n.tagName !== m[1].toUpperCase()) return false;
  const bits = m[2].match(/[.#][\w-]+/g) || [];
  for (const b of bits) {
    if (b[0] === "." && !n._classes().includes(b.slice(1))) return false;
    if (b[0] === "#" && n.id !== b.slice(1)) return false;
  }
  const attrs = m[3].match(/\[[^\]]+\]/g) || [];
  for (const a of attrs) {
    const am = /^\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]$/.exec(a);
    if (!(am[1] in n.attributes)) return false;
    if (am[2] !== undefined && n.attributes[am[1]] !== am[2]) return false;
  }
  return true;
}

class Document extends Element {
  constructor() {
    super(null, "#document");
    this.ownerDocument = this;
    this.readyState = "complete";
    this.title = "";
    this.documentElement = new Element(this, "html");
    this.head = new Element(this, "head");
    this.body = new Element(this, "body");
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
    this.appendChild(this.documentElement);
  }
  createElement(tag) { return new Element(this, tag); }
  createElementNS(ns, tag) { return new Element(this, tag); }
  createTextNode(text) { return new Text(this, text); }
  createDocumentFragment() { return new Element(this, "#fragment"); }
  getElementById(id) {
    let found = this.querySelector("#" + id);
    if (!found) { found = this.createElement("div"); found.id = id; this.body.appendChild(found); }
    return found;
  }
}

module.exports = {Document, Element, Text};
