"use strict";
/* KI-Auswertung: Texte abrufen und in die Karten [data-ai] einsetzen.
   Karte: <section data-ai="drive" hidden> mit Unterelement .ai-body (data-ai="summary compare tips" = mehrere Schlüssel).
   KI-Text kommt nie als HTML in die Seite, nur als textContent. */
(() => {
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key, vars) => String(I[key] != null ? I[key] : key).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m));
  const POLL_MS = 5000, POLL_MAX_MS = 120000;
  let last = null, timer = null, run = 0;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  function renderCard(card, resp) {
    const body = card.querySelector(".ai-body") || card;
    body.textContent = "";
    const st = resp && resp.status;
    if (!resp || st === "off" || st === "none") { card.hidden = true; return; }
    const head = el("div", "ai-head");
    head.appendChild(el("span", "ai-tag", tr("ai.label")));
    if (resp.model) head.appendChild(el("span", "ai-model", resp.model));
    body.appendChild(head);
    const c = resp.content || {};
    const keys = (card.dataset.ai || "").split(/\s+/).filter(Boolean);
    let shown = 0;
    if (st === "ready" || st === "stale") {
      keys.forEach((k) => {
        if (k === "tips") {
          const tips = (Array.isArray(c.tips) ? c.tips : []).filter((x) => typeof x === "string" && x.trim());
          if (!tips.length) return;
          body.appendChild(el("h3", "ai-sub", tr("ai.tips")));
          const ul = el("ul", "ai-tips");
          tips.forEach((x) => ul.appendChild(el("li", null, x)));
          body.appendChild(ul);
          shown++;
        } else if (typeof c[k] === "string" && c[k].trim()) {
          body.appendChild(el("p", "ai-text", c[k]));
          shown++;
        }
      });
      if (!shown) { card.hidden = true; return; }
      if (st === "stale") body.appendChild(el("p", "ai-note muted small", tr("ai.stale")));
    } else if (st === "pending") {
      body.appendChild(el("p", "ai-note muted", tr("ai.pending")));
    } else if (st === "error") {
      const p = el("p", "ai-note muted", tr("ai.error", { m: resp.error || "?" }) + " ");
      const a = el("a", null, tr("ai.to_settings"));
      a.href = "/settings#sec-ai";
      p.appendChild(a);
      body.appendChild(p);
    } else { card.hidden = true; return; }
    card.hidden = false;
  }

  function apply() {
    document.querySelectorAll("[data-ai]").forEach((card) => renderCard(card, last));
  }

  function stop() { run++; if (timer) { clearTimeout(timer); timer = null; } }

  // Ruft url ab; bei pending/stale alle 5 s erneut, höchstens 2 Minuten.
  function watch(url) {
    stop();
    const mine = run, started = Date.now();
    const poll = () => {
      fetch(url, { credentials: "same-origin" })
        .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then((d) => {
          if (mine !== run) return;
          last = d;
          apply();
          if ((d.status === "pending" || d.status === "stale") && Date.now() - started < POLL_MAX_MS) timer = setTimeout(poll, POLL_MS);
        })
        .catch(() => { if (mine === run) { last = null; apply(); } });
    };
    poll();
  }

  window.avinoxAi = { watch, apply, stop, lang: I.lang === "en" ? "en" : "de" };
})();
