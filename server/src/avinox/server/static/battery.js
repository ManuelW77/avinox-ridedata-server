/* Akku-Verlauf: vier kleine Diagramme je Sitzung (uPlot), Cursor synchronisiert. */
(function () {
  "use strict";
  const dataEl = document.getElementById("bat-data");
  if (!dataEl || typeof uPlot === "undefined") return;
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const LOCALE = I.lang === "en" ? "en-US" : "de-DE";
  let rows;
  try { rows = JSON.parse(dataEl.textContent); } catch (e) { return; }
  if (!rows || rows.length < 2) return;
  // Start 0 % zusammen mit 0 °C heißt: Startwerte fehlen (Display hat sie nicht gespeichert)
  rows.forEach((r) => {
    if (r.start_pct === 0 && r.start_temp_c === 0) { r.start_pct = null; r.start_temp_c = null; }
  });

  const cssVar = (name, fallback) => (getComputedStyle(document.documentElement).getPropertyValue(name) || "").trim() || fallback;
  const fmt = (v, d) => (v == null || !isFinite(v)) ? "–"
    : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  const pad = (n) => String(n).padStart(2, "0");
  function day(r, withYear) {
    if (!r.time) return r.boot_count != null ? "#" + r.boot_count : "";
    const t = new Date(r.time);
    if (isNaN(t)) return "";
    return pad(t.getDate()) + "." + pad(t.getMonth() + 1) + "." + (withYear ? pad(t.getFullYear() % 100) : "");
  }
  const label = (r) => day(r, false) || "";

  const xs = rows.map((_, i) => i);
  const col = (k) => rows.map((r) => (r[k] == null ? null : r[k]));
  const HOT = 45, COLD = 5; // wie COLD_C / HOT_C in battery.py

  const C = {
    motor: cssVar("--motor", "#13877a"), alt: cssVar("--alt", "#6b6f9a"), ink3: cssVar("--ink-3", "#8a908c"),
    line: cssVar("--line", "#dcd8cf"), warn: cssVar("--warn", "#c0392b"), font: "11px " + cssVar("--font", "sans-serif"),
  };

  const charts = [];
  const defs = [
    { el: "bat-c-charge", out: "bat-v-charge", range: [0, 100],
      series: [{ k: "end_pct", color: C.motor, points: true }, { k: "start_pct", color: C.ink3, dash: [4, 4] }],
      text: (r) => fmt(r.start_pct) + " % → " + fmt(r.end_pct) + " %" },
    { el: "bat-c-temp", out: "bat-v-temp",
      series: [{ k: "max_temp_c", color: C.alt, points: true, warn: (v) => v >= HOT || v < COLD }, { k: "start_temp_c", color: C.ink3, dash: [4, 4] }],
      text: (r) => fmt(r.max_temp_c) + " °C" },
    { el: "bat-c-delta", out: "bat-v-delta", series: [{ k: "max_cell_delta_mv", color: C.motor, points: true }],
      text: (r) => fmt(r.max_cell_delta_mv) + " mV" },
    { el: "bat-c-current", out: "bat-v-current", series: [{ k: "max_current_a", color: C.ink3, points: true }],
      text: (r) => fmt(r.max_current_a, 1) + " A" },
  ];

  function show(def, i) {
    const out = document.getElementById(def.out);
    if (!out) return;
    const r = rows[i == null ? rows.length - 1 : i];
    const d = day(r, true);
    out.textContent = def.text(r) + (d ? " · " + d : "");
  }

  defs.forEach((def, n) => {
    const el = document.getElementById(def.el);
    if (!el) return;
    const last = n === defs.length - 1;
    const axis = { stroke: C.ink3, grid: { stroke: C.line, width: 1 }, ticks: { stroke: C.line, width: 1 }, font: C.font };
    const opts = {
      width: el.clientWidth || 300, height: 140,
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, sync: { key: "bat-hist" }, points: { size: 6 } },
      scales: { x: { time: false }, y: def.range ? { range: def.range } : {} },
      axes: [
        Object.assign({}, axis, last
          ? { values: (u, vals) => vals.map((v) => (Number.isInteger(v) && rows[v] ? label(rows[v]) : "")), size: 34 }
          : { values: (u, vals) => vals.map(() => ""), size: 8 }),
        Object.assign({}, axis, { size: 44, values: (u, vals) => vals.map((v) => fmt(v, Number.isInteger(v) ? 0 : 1)) }),
      ],
      series: [{}].concat(def.series.map((s) => ({
        stroke: s.color, width: s.points ? 2 : 1.5, dash: s.dash, spanGaps: false,
        points: s.points ? {
          show: true, size: 6, fill: s.color,
          stroke: (u, si) => s.color,
        } : { show: false },
      }))),
      hooks: {
        setCursor: [(u) => show(def, u.cursor.idx == null ? null : u.cursor.idx)],
      },
    };
    const data = [xs].concat(def.series.map((s) => col(s.k)));
    // Warnfarbe: Werte jenseits der Schwellen als eigene Punktserie obendrauf
    // (uPlot färbt Punkte nur je Serie, nicht je Punkt)
    def.series.forEach((s) => {
      if (!s.warn) return;
      data.push(col(s.k).map((v) => (v != null && s.warn(v) ? v : null)));
      opts.series.push({ stroke: C.warn, width: 0, paths: () => null,
        points: { show: true, size: 8, fill: C.warn, stroke: C.warn } });
    });
    const u = new uPlot(opts, data, el);
    charts.push({ u, el });
    show(def, null);
  });

  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => charts.forEach((c) => {
      const w = c.el.clientWidth;
      if (w > 0 && w !== c.u.width) c.u.setSize({ width: w, height: 140 });
    }));
    charts.forEach((c) => ro.observe(c.el));
  }
})();
