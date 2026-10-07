// Inline SVG icons (CSP allows no external images or fonts). Shapes follow the Feather icon
// style (MIT). Built with createElementNS from constant data, never from strings in innerHTML.
(function () {
  "use strict";
  var KB = (window.KB = window.KB || {});
  var NS = "http://www.w3.org/2000/svg";

  function p(d) { return ["path", {d: d}]; }
  function c(cx, cy, r) { return ["circle", {cx: cx, cy: cy, r: r}]; }
  function r(x, y, w, h, rx) { return ["rect", {x: x, y: y, width: w, height: h, rx: rx || 0}]; }

  var ICONS = {
    kestrel: {fill: true, parts: [
      p("M2.5 13.2c3.6-.4 6.6-2.6 8.6-7.7.7 3.6 3.1 5.7 7.4 6.1l3 .3-2.6 1.4c-2.3 1.2-4.6 1.8-6.9 1.9l-2.7 5.3-.7-4.9c-2.2-.2-4.2-1-6.1-2.4z"),
      c(16.6, 9.6, 0.9)
    ]},
    moon: {parts: [p("M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z")]},
    sun: {parts: [c(12, 12, 5), p("M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42")]},
    home: {parts: [p("M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"), p("M9 22V12h6v10")]},
    chat: {parts: [p("M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z")]},
    inbox: {parts: [p("M22 12h-6l-2 3h-4l-2-3H2"), p("M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z")]},
    scale: {parts: [p("M12 3v18M7 21h10M5 7h14M5 7l-3 7a3 3 0 0 0 6 0zM19 7l-3 7a3 3 0 0 0 6 0z")]},
    chart: {parts: [p("M18 20V10M12 20V4M6 20v-6")]},
    target: {parts: [c(12, 12, 10), c(12, 12, 6), c(12, 12, 2)]},
    usercheck: {parts: [p("M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"), c(8.5, 7, 4), p("M17 11l2 2 4-4")]},
    users: {parts: [p("M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"), c(9, 7, 4), p("M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75")]},
    activity: {parts: [p("M22 12h-4l-3 9L9 3l-3 9H2")]},
    shield: {parts: [p("M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z")]},
    shieldcheck: {parts: [p("M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"), p("M9 12l2 2 4-4")]},
    toggle: {parts: [r(1, 5, 22, 14, 7), c(16, 12, 3)]},
    columns: {parts: [p("M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7m0-18H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h7m0-18v18")]},
    send: {parts: [p("M22 2L11 13"), p("M22 2l-7 20-4-9-9-4z")]},
    external: {parts: [p("M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"), p("M15 3h6v6"), p("M10 14L21 3")]},
    lock: {parts: [r(3, 11, 18, 11, 2), p("M7 11V7a5 5 0 0 1 10 0v4")]},
    cpu: {parts: [r(4, 4, 16, 16, 2), r(9, 9, 6, 6, 0), p("M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3")]},
    cloud: {parts: [p("M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z")]},
    alert: {parts: [p("M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"), p("M12 9v4M12 17h.01")]},
    info: {parts: [c(12, 12, 10), p("M12 16v-4M12 8h.01")]},
    tool: {parts: [p("M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z")]},
    node: {parts: [p("M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"), p("M3.27 6.96L12 12.01l8.73-5.05M12 22.08V12")]},
    sparkle: {parts: [p("M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"), p("M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z")]},
    search: {parts: [c(11, 11, 8), p("M21 21l-4.35-4.35")]},
    database: {parts: [["ellipse", {cx: 12, cy: 5, rx: 9, ry: 3}], p("M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"), p("M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5")]},
    tag: {parts: [p("M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"), p("M7 7h.01")]},
    trending: {parts: [p("M23 6l-9.5 9.5-5-5L1 18"), p("M17 6h6v6")]},
    route: {parts: [p("M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5")]},
    bell: {parts: [p("M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"), p("M13.73 21a2 2 0 0 1-3.46 0")]},
    checkcircle: {parts: [p("M22 11.08V12a10 10 0 1 1-5.93-9.14"), p("M22 4L12 14.01l-3-3")]},
    clock: {parts: [c(12, 12, 10), p("M12 6v6l4 2")]},
    refresh: {parts: [p("M23 4v6h-6"), p("M20.49 15a9 9 0 1 1-2.12-9.36L23 10")]},
    plus: {parts: [p("M12 5v14M5 12h14")]},
    x: {parts: [p("M18 6L6 18M6 6l12 12")]},
    check: {parts: [p("M20 6L9 17l-5-5")]},
    logout: {parts: [p("M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"), p("M16 17l5-5-5-5M21 12H9")]},
    eyeoff: {parts: [p("M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"), p("M1 1l22 22")]},
    chevron: {parts: [p("M9 18l6-6-6-6")]},
    grid: {parts: [r(3, 3, 7, 7, 1), r(14, 3, 7, 7, 1), r(14, 14, 7, 7, 1), r(3, 14, 7, 7, 1)]},
    doc: {parts: [p("M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"), p("M14 2v6h6M16 13H8M16 17H8M10 9H8")]},
    copy: {parts: [r(9, 9, 13, 13, 2), p("M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1")]},
    hash: {parts:[p("M4 9h16M4 15h16M10 3L8 21M16 3l-2 18")]},
    link: {parts: [p("M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"), p("M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71")]}
  };

  KB.ICONS = ICONS;

  KB.icon = function (name, cls) {
    var spec = ICONS[name] || ICONS.info;
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    if (spec.fill) {
      svg.setAttribute("fill", "currentColor");
      svg.setAttribute("stroke", "none");
    } else {
      svg.setAttribute("fill", "none");
      svg.setAttribute("stroke", "currentColor");
      svg.setAttribute("stroke-width", "2");
      svg.setAttribute("stroke-linecap", "round");
      svg.setAttribute("stroke-linejoin", "round");
    }
    spec.parts.forEach(function (part) {
      var node = document.createElementNS(NS, part[0]);
      Object.keys(part[1]).forEach(function (k) { node.setAttribute(k, String(part[1][k])); });
      svg.appendChild(node);
    });
    return svg;
  };

  // Static placeholders in index.html: <span data-icon="moon"></span>
  KB.hydrateIcons = function (root) {
    var nodes = (root || document).querySelectorAll("[data-icon]");
    Array.prototype.forEach.call(nodes, function (n) {
      while (n.firstChild) n.removeChild(n.firstChild);
      n.appendChild(KB.icon(n.getAttribute("data-icon")));
    });
  };
})();
