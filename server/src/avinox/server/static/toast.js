"use strict";
(() => {
  // Texte aus <script id="i18n"> (siehe base.html)
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const reduced = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const NS = "http://www.w3.org/2000/svg";

  // ---- Toast: kleine Hinweis-Pille unten mittig
  let el = null, timer = null, hideTimer = null;
  function icon(kind) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", "16"); svg.setAttribute("height", "16");
    svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor"); svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round"); svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", kind === "error" ? "M12 8v5M12 17h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.700 3.900a2 2 0 0 0-3.400 0z" : "M5 12.500l4.500 4.500L19 7.500");
    svg.appendChild(path);
    return svg;
  }
  window.avinoxToast = (text, kind) => {
    kind = kind === "error" ? "error" : "ok";
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    clearTimeout(timer); clearTimeout(hideTimer);
    el.className = "toast " + kind;
    el.textContent = "";
    el.appendChild(icon(kind));
    const span = document.createElement("span");
    span.textContent = text;
    el.appendChild(span);
    void el.offsetWidth; // Übergang neu starten
    el.classList.add("show");
    timer = setTimeout(() => { el.classList.remove("show"); }, 2500);
  };

  // Einmaliger Hinweis nach Seitenneuladen (Sprache gespeichert)
  try {
    if (sessionStorage.getItem("avinox-toast") === "lang") {
      sessionStorage.removeItem("avinox-toast");
      window.avinoxToast(I.lang_saved || "OK");
    }
  } catch (e) {}

  // ---- Nach-oben-Knopf
  const btn = document.getElementById("to-top");
  if (btn) {
    let ticking = false, shown = false;
    const apply = () => {
      ticking = false;
      const want = window.scrollY > 400;
      if (want === shown) return;
      shown = want;
      if (want) {
        btn.hidden = false;
        void btn.offsetWidth;
        btn.classList.add("show");
      } else {
        btn.classList.remove("show");
        setTimeout(() => { if (!shown) btn.hidden = true; }, reduced() ? 0 : 200);
      }
    };
    window.addEventListener("scroll", () => {
      if (!ticking) { ticking = true; requestAnimationFrame(apply); }
    }, { passive: true });
    btn.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
      const target = document.querySelector("main h1") || document.querySelector("main");
      if (target) {
        if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: true });
      }
    });
    apply();
  }
})();
