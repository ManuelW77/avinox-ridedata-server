// Schnellsprung: baut aus den Blöcken mit [data-jump] eine Navigation
// (rechts fixiert auf breiten Bildschirmen, sonst eine Chip-Leiste oben).
(function () {
  "use strict";
  var main = document.querySelector("main");
  var blocks = Array.prototype.slice.call(document.querySelectorAll("[data-jump]"));
  if (!main || blocks.length < 2) return;

  var label = "Schnellsprung";
  try { label = JSON.parse(document.getElementById("i18n").textContent)["jump.label"] || label; } catch (e) { /* Standardtext */ }

  var nav = document.createElement("nav");
  nav.className = "jumpnav";
  nav.setAttribute("aria-label", label);
  main.insertBefore(nav, main.firstChild);

  var links = [];
  var current = null;

  function visible(el) { return !el.hidden && el.getClientRects().length > 0; }

  function build() {
    nav.textContent = "";
    links = [];
    blocks.forEach(function (b) {
      if (!visible(b)) return;
      var a = document.createElement("a");
      a.href = "#" + b.id;
      a.textContent = b.getAttribute("data-jump");
      nav.appendChild(a);
      links.push({ a: a, el: b });
    });
    nav.hidden = links.length < 2;
    mark();
  }

  function mark() {
    // Aktiv ist der letzte Block, dessen Oberkante über der Leseline liegt.
    var line = window.innerHeight * 0.3, cur = links[0];
    links.forEach(function (l) { if (l.el.getBoundingClientRect().top <= line) cur = l; });
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) cur = links[links.length - 1];
    if (cur === current) return;
    current = cur;
    links.forEach(function (l) {
      if (l === cur) l.a.setAttribute("aria-current", "true"); else l.a.removeAttribute("aria-current");
    });
    if (cur && nav.scrollWidth > nav.clientWidth) {
      var r = cur.a, left = r.offsetLeft - nav.clientWidth / 2 + r.offsetWidth / 2;
      nav.scrollLeft = left; // nur die Leiste scrollen, nicht die Seite
    }
  }

  var queued = false;
  function onScroll() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; mark(); });
  }

  build();
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  // Blöcke, die erst per Skript eingeblendet werden (z. B. Verteilung), neu erfassen.
  var mo = new MutationObserver(function () { current = null; build(); });
  blocks.forEach(function (b) { mo.observe(b, { attributes: true, attributeFilter: ["hidden"] }); });
})();
