/* Übersicht: Kennzahlen des Zeitraums mit Vergleich und Diagramme je Tag/Woche/Monat/Jahr (uPlot). */
(function () {
  "use strict";

  const dataEl = document.getElementById("dash-data");
  if (!dataEl) return;
  let dash;
  try { dash = JSON.parse(dataEl.textContent); } catch (e) { return; }

  const $ = (id) => document.getElementById(id);
  const cssVar = (name, fallback) => (getComputedStyle(document.documentElement).getPropertyValue(name) || "").trim() || fallback;
  const fmt = (v, digits) => (v == null || !isFinite(v)) ? "–"
    : Number(v).toLocaleString("de-DE", { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 });

  function hm(sec) {
    if (sec == null || !isFinite(sec)) return "–";
    const m = Math.round(sec / 60);
    return Math.floor(m / 60) + ":" + String(m % 60).padStart(2, "0");
  }

  function withAlpha(color, a) {
    const m = /^#([0-9a-f]{6})$/i.exec(color.trim());
    if (!m) return color;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  /* --------------------------------------------------------- Kennzahlen */

  // key, Bezeichnung, Formatierer, Klasse, Richtung bewerten?
  const KPIS = [
    ["rides", "Fahrten", (v) => fmt(v, 0), "", true],
    ["moving_time_s", "Fahrzeit", (v) => hm(v) + "<small>h</small>", "", true],
    ["ascent_m", "Höhenmeter", (v) => fmt(v, 0) + "<small>m</small>", "", true],
    ["rider_kj", "Eigenleistung", (v) => fmt(v, 0) + "<small>kJ</small>", "rider", true],
    ["avg_rider_power_w", "Ø Fahrerleistung", (v) => fmt(v, 0) + "<small>W</small>", "rider", true],
    ["motor_wh", "Motorenergie", (v) => fmt(v, 0) + "<small>Wh</small>", "motor", false],
    ["motor_share_pct", "Motoranteil", (v) => fmt(v, 0) + "<small>%</small>", "motor", false],
    ["battery_used_pct", "Akkuverbrauch", (v) => fmt(v, 0) + "<small>%</small>", "", false],
    ["calories_kcal", "Kalorien", (v) => fmt(v, 0) + "<small>kcal</small>", "", true],
  ];

  // Prozentpunkte statt Prozent bei Anteilen
  const POINTS = { motor_share_pct: true };

  function deltaHtml(key, c, judge) {
    if (!c || c.current == null || c.previous == null) return '<span class="d none">kein Vergleich</span>';
    if (POINTS[key]) {
      const d = c.current - c.previous;
      if (Math.abs(d) < 0.5) return '<span class="d same">wie davor</span>';
      return `<span class="d ${judge ? (d > 0 ? "up" : "down") : "neutral"}">${d > 0 ? "↑" : "↓"} ${fmt(Math.abs(d), 0)} Pkt.</span>`;
    }
    if (!c.previous) return c.current ? '<span class="d none">davor nichts</span>' : '<span class="d none">–</span>';
    const p = c.delta_pct;
    if (p == null || p === 0) return '<span class="d same">wie davor</span>';
    return `<span class="d ${judge ? (p > 0 ? "up" : "down") : "neutral"}">${change(c)}</span>`;
  }

  // „↑ 18 %“, ab Verdopplung als Faktor („↑ 3,2×“) – große Prozentzahlen liest niemand.
  function change(c) {
    const p = c.delta_pct || 0;
    if (p >= 100 && c.previous) {
      const f = c.current / c.previous;
      return "↑ " + fmt(f, f < 10 ? 1 : 0) + "×";
    }
    return (p > 0 ? "↑ " : p < 0 ? "↓ " : "→ ") + fmt(Math.abs(p), 0) + " %";
  }

  function renderPeriod() {
    const cur = dash.current, cmp = dash.comparison;
    document.querySelectorAll("#period-by button[data-key]").forEach((b) =>
      b.setAttribute("aria-pressed", b.dataset.key === dash.period ? "true" : "false"));
    $("period-range").textContent = cur.title;
    $("period-prev").disabled = dash.offset >= dash.max_offset;
    $("period-next").disabled = dash.offset <= 0;
    $("hero-name").textContent = cur.name;
    $("hero-km").textContent = fmt(cur.distance_km, 1);

    const d = cmp.distance_km;
    let delta = "";
    if (d.previous && !cur.rides) {
      delta = `${dash.versus.replace("ggü. ", "")}: ${fmt(d.previous, 1)} km`;
    } else if (d.previous) {
      const p = d.delta_pct || 0;
      const cls = p > 0 ? "up" : p < 0 ? "down" : "same";
      delta = `<span class="d ${cls}">${change(d)}</span> ${dash.versus} (${fmt(d.previous, 1)} km)`;
    } else if (cur.distance_km) {
      delta = `Im Zeitraum davor keine Fahrt.`;
    }
    $("hero-delta").innerHTML = delta;

    const energy = (cur.rider_kj || 0) + (cur.motor_kj || 0);
    const split = $("hero-split");
    split.hidden = !energy || !cur.rides;
    if (energy) {
      const rp = 100 * cur.rider_kj / energy;
      split.querySelector(".rider").style.width = rp + "%";
      split.querySelector(".motor").style.width = (100 - rp) + "%";
      $("split-rider").innerHTML = `<i></i>Du ${fmt(rp, 0)} % · ${fmt(cur.rider_kj, 0)} kJ`;
      $("split-motor").innerHTML = `<i></i>Motor ${fmt(100 - rp, 0)} % · ${fmt(cur.motor_kj, 0)} kJ`;
    }
    const empty = $("hero-empty");
    empty.hidden = cur.rides > 0;
    empty.textContent = "";
    if (!cur.rides) {
      empty.appendChild(document.createTextNode("In diesem Zeitraum keine Fahrt."));
      if (dash.last_offset != null && dash.last_offset !== dash.offset) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn ghost small-btn";
        b.textContent = "Zur letzten Fahrt springen";
        b.addEventListener("click", () => load(dash.period, dash.last_offset));
        empty.appendChild(b);
      }
    }

    $("kpis").hidden = !cur.rides;
    $("kpis").innerHTML = KPIS.map(([key, label, f, cls, judge]) => {
      const v = cur[key];
      return `<div class="kpi ${cls}"><div class="v">${v == null ? "–" : f(v)}</div><div class="k">${label}</div>${deltaHtml(key, cmp[key], judge)}</div>`;
    }).join("");

    const sum = (k) => dash.series.reduce((a, s) => a + (s[k] || 0), 0);
    const span = { day: "30 Tagen", week: "26 Wochen", month: "24 Monaten", year: "5 Jahren" }[dash.period];
    $("sum-distance").textContent = `${fmt(sum("distance_km"), 0)} km in ${span}`;
    $("sum-ascent").textContent = `${fmt(sum("ascent_m"), 0)} m in ${span}`;
  }

  /* --------------------------------------------------------- Diagramme */

  let charts = [];

  function colors() {
    return {
      rider: cssVar("--rider", "#d9661a"), motor: cssVar("--motor", "#13877a"), alt: cssVar("--alt", "#6b6f9a"),
      ink2: cssVar("--ink-2", "#57605f"), ink3: cssVar("--ink-3", "#8a908c"), line: cssVar("--line", "#dcd8cf"),
      surface2: cssVar("--surface-2", "#ebe8e1"), font: "11px " + cssVar("--font", "sans-serif"),
    };
  }

  function makeChart(el, cfg) {
    const c = colors();
    const n = dash.series.length;
    const xs = dash.series.map((_, i) => i);
    const labels = dash.series.map((s) => s.label);
    const titles = dash.series.map((s) => s.title);
    const width = Math.max(240, el.clientWidth || 500);

    const splits = (u) => {
      const room = Math.max(1, Math.floor(u.bbox.width / devicePixelRatio / 52));
      const step = Math.max(1, Math.ceil(n / room));
      const out = [];
      for (let i = n - 1; i >= 0; i -= step) out.unshift(i);
      return out;
    };

    const opts = {
      width, height: width < 420 ? 170 : 200,
      padding: [8, 16, 0, 0],
      cursor: { drag: { x: false, y: false }, points: { show: false }, y: false },
      legend: { show: false },
      scales: { x: { time: false, range: [-0.5, n - 0.5] }, y: cfg.yRange ? { range: cfg.yRange } : { range: (u, min, max) => [0, max > 0 ? max * 1.12 : 1] } },
      series: [{ label: "Zeitraum", value: (u, v) => (v == null ? "" : titles[v] || "") }].concat(cfg.series),
      axes: [
        { stroke: c.ink2, font: c.font, grid: { show: false }, ticks: { show: false }, splits, values: (u, vs) => vs.map((v) => labels[v] || ""), gap: 6, size: 28 },
        { stroke: c.ink3, font: c.font, grid: { stroke: withAlpha(c.line.startsWith("#") ? c.line : "#dcd8cf", 0.7), width: 1 }, ticks: { show: false },
          size: 44, values: (u, vs) => vs.map((v) => fmt(v, 0) + (cfg.unitTick || "")), splits: cfg.ySplits },
      ],
      hooks: {
        // Ausgewählten Zeitraum hinterlegen
        drawClear: [(u) => {
          const ctx = u.ctx;
          const x0 = u.valToPos(n - 1.5, "x", true), x1 = u.valToPos(n - 0.5, "x", true);
          ctx.save();
          ctx.fillStyle = c.surface2;
          ctx.fillRect(x0, u.bbox.top, x1 - x0, u.bbox.height);
          ctx.restore();
        }],
      },
    };
    const readout = cfg.readout;
    const show = (i) => {
      if (!readout) return;
      const parts = cfg.series.map((sr, k) => {
        const v = cfg.data[k][i];
        const txt = sr.value ? sr.value(null, v, k + 1, i) : fmt(v, 0);
        return `<i style="background:${sr.fill && !String(sr.fill).startsWith("rgba") ? sr.fill : sr.stroke}"></i>${sr.label} <b>${txt}</b>`;
      });
      readout.innerHTML = `${titles[i] || ""}${parts.join("")}`;
    };
    opts.hooks.setCursor = [(u) => show(u.cursor.idx == null ? n - 1 : u.cursor.idx)];
    const u = new uPlot(opts, [xs].concat(cfg.data), el);
    show(n - 1);
    u.over.style.cursor = "pointer";
    u.over.addEventListener("click", () => {
      const i = u.cursor.idx;
      if (i == null) return;
      const off = dash.offset + (n - 1 - i);
      if (off !== dash.offset) load(dash.period, off);
    });
    return u;
  }

  function bars(size, align) {
    return uPlot.paths && uPlot.paths.bars ? uPlot.paths.bars({ size, align, radius: 0.2 }) : undefined;
  }

  function buildCharts() {
    charts.forEach((u) => u.destroy());
    charts = [];
    if (typeof uPlot === "undefined") {
      document.querySelectorAll(".dchart").forEach((el) => { el.innerHTML = '<p class="muted small">Diagramme konnten nicht geladen werden.</p>'; });
      return;
    }
    const c = colors();
    const s = dash.series;
    const val = (unit, digits) => (u, v) => (v == null ? "–" : fmt(v, digits) + " " + unit);
    const anyRide = s.some((x) => x.rides > 0);
    const defs = [
      ["chart-distance", {
        data: [s.map((x) => x.distance_km)],
        series: [{ label: "Strecke", stroke: c.alt, fill: c.alt, width: 0, paths: bars([0.62, 40], 0), points: { show: false }, value: val("km", 1) }],
      }],
      ["chart-energy", {
        data: [s.map((x) => x.rider_kj), s.map((x) => x.motor_kj)],
        series: [
          { label: "Fahrer", stroke: c.rider, fill: c.rider, width: 0, paths: bars([0.36, 22], -1), points: { show: false }, value: val("kJ", 0) },
          { label: "Motor", stroke: c.motor, fill: c.motor, width: 0, paths: bars([0.36, 22], 1), points: { show: false },
            value: (u, v, si, i) => (v == null ? "–" : fmt(v, 0) + " kJ (" + fmt(s[i] && s[i].motor_wh, 0) + " Wh)") },
        ],
      }],
      ["chart-share", {
        data: [s.map((x) => x.motor_share_pct)],
        yRange: [0, 100], unitTick: " %", ySplits: () => [0, 25, 50, 75, 100],
        series: [{ label: "Motoranteil", stroke: c.motor, width: 2, spanGaps: true,
          points: { show: true, size: 6, fill: c.motor }, value: val("%", 0) }],
      }],
      ["chart-ascent", {
        data: [s.map((x) => x.ascent_m)],
        series: [{ label: "Höhenmeter", stroke: c.ink3, fill: c.ink3, width: 0, paths: bars([0.62, 40], 0), points: { show: false }, value: val("m", 0) }],
      }],
    ];
    defs.forEach(([id, cfg]) => {
      const el = $(id);
      if (!el) return;
      el.textContent = "";
      if (!anyRide) {
        el.innerHTML = '<div class="chart-empty">Keine Fahrten in diesem Zeitraum.</div>';
        return;
      }
      cfg.readout = $(id.replace("chart-", "readout-"));
      if (cfg.readout) cfg.readout.textContent = "";
      try { charts.push(makeChart(el, cfg)); } catch (e) { el.innerHTML = '<p class="muted small">Diagramm nicht verfügbar.</p>'; }
    });
  }

  /* --------------------------------------------------------- Laden */

  let pending = null;
  function load(period, offset) {
    const url = `/api/v1/dashboard?period=${encodeURIComponent(period)}&offset=${offset}`;
    const token = pending = {};
    $("period").classList.add("loading");
    fetch(url, { credentials: "same-origin" })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((d) => {
        if (token !== pending) return;
        dash = d;
        const q = new URLSearchParams(location.search);
        q.set("period", d.period);
        if (d.offset) q.set("offset", d.offset); else q.delete("offset");
        history.replaceState(null, "", location.pathname + "?" + q.toString());
        renderPeriod();
        buildCharts();
      })
      .catch(() => {})
      .finally(() => { if (token === pending) $("period").classList.remove("loading"); });
  }

  document.querySelectorAll("#period-by button[data-key]").forEach((b) =>
    b.addEventListener("click", () => { if (b.dataset.key !== dash.period) load(b.dataset.key, 0); }));
  $("period-prev").addEventListener("click", () => load(dash.period, dash.offset + 1));
  $("period-next").addEventListener("click", () => load(dash.period, Math.max(0, dash.offset - 1)));
  document.querySelectorAll("a.best-link[data-period]").forEach((a) =>
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      load(a.dataset.period, Number(a.dataset.offset) || 0);
      $("period").scrollIntoView({ behavior: "smooth", block: "start" });
    }));

  let resizeTimer = null;
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(buildCharts, 150);
  });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    (mq.addEventListener ? mq.addEventListener.bind(mq, "change") : mq.addListener.bind(mq))(buildCharts);
  }

  renderPeriod();
  buildCharts();
})();
