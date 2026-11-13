/* Übersicht: Kennzahlen des Zeitraums mit Vergleich und Diagramme je Tag/Woche/Monat/Jahr (uPlot). */
(function () {
  "use strict";

  const dataEl = document.getElementById("dash-data");
  if (!dataEl) return;
  // Texte aus <script id="i18n"> (siehe base.html), Sprache in I.lang
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key, vars) => String(I[key] != null ? I[key] : key).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m));
  const LOCALE = I.lang === "en" ? "en-US" : "de-DE";
  let dash;
  try { dash = JSON.parse(dataEl.textContent); } catch (e) { return; }

  // Fallback, falls das Backend rider_wh (noch) nicht liefert: kJ / 3,6
  function addWh(o) { if (o && o.rider_wh == null && o.rider_kj != null) o.rider_wh = o.rider_kj / 3.6; }
  function normalize(d) {
    try {
      [d.current, d.previous, d.total].forEach(addWh);
      (d.series || []).forEach(addWh);
      (d.recent || []).forEach(addWh);
      const c = d.comparison;
      if (c && !c.rider_wh && c.rider_kj) {
        c.rider_wh = Object.assign({}, c.rider_kj, {
          current: c.rider_kj.current == null ? null : c.rider_kj.current / 3.6,
          previous: c.rider_kj.previous == null ? null : c.rider_kj.previous / 3.6 });
      }
    } catch (e) { /* ignorieren */ }
    return d;
  }
  normalize(dash);

  // Vergleichsmodus (gleicher Stand / ganzer Zeitraum), im Browser gemerkt
  const CMP_KEY = "avinox.compare";
  let cmp = "elapsed";
  try {
    const fromUrl = new URLSearchParams(location.search).get("compare");
    const stored = localStorage.getItem(CMP_KEY);
    cmp = fromUrl === "full" || fromUrl === "elapsed" ? fromUrl : (stored === "full" ? "full" : "elapsed");
  } catch (e) { /* ohne Speicher */ }
  const day10 = (v) => (v ? String(v).slice(0, 10) : "");
  const deDate = (iso) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return m ? m[3] + "." + m[2] + "." + m[1] : "";
  };
  const isOpen = (x) => !!(x && x.end && dash.today && day10(x.end) >= day10(dash.today));

  const $ = (id) => document.getElementById(id);
  const cssVar = (name, fallback) => (getComputedStyle(document.documentElement).getPropertyValue(name) || "").trim() || fallback;
  const fmt = (v, digits) => (v == null || !isFinite(v)) ? "–"
    : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 });

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
    ["rides", tr("kpi.rides"), (v) => fmt(v, 0), "", true],
    ["moving_time_s", tr("kpi.moving"), (v) => hm(v) + "<small>h</small>", "", true],
    ["ascent_m", tr("kpi.ascent"), (v) => fmt(v, 0) + "<small>m</small>", "", true],
    ["rider_wh", tr("kpi.rider"), (v) => fmt(v, 0) + "<small>Wh</small>", "rider", true],
    ["avg_rider_power_w", tr("kpi.avg_rider"), (v) => fmt(v, 0) + "<small>W</small>", "rider", true],
    ["motor_wh", tr("kpi.motor_wh"), (v) => fmt(v, 0) + "<small>Wh</small>", "motor", false],
    ["motor_share_pct", tr("kpi.share"), (v) => fmt(v, 0) + "<small>%</small>", "motor", false],
    ["battery_used_pct", tr("kpi.battery"), (v) => fmt(v, 0) + "<small>%</small>", "", false],
    ["calories_kcal", tr("kpi.calories"), (v) => fmt(v, 0) + "<small>kcal</small>", "", true],
  ];

  // Prozentpunkte statt Prozent bei Anteilen
  const POINTS = { motor_share_pct: true };

  // Vergleichsbasis mit weniger Fahrten ist zu klein für ein farbiges Delta
  const MIN_COMPARE_RIDES = 3;
  const prevTiny = () => ((dash.comparison.rides || {}).previous || 0) < MIN_COMPARE_RIDES;

  function deltaHtml(key, c, judge, fmtPrev) {
    if (dash.period === "all") return "";
    if (!c || c.current == null || c.previous == null) return '<span class="d none">' + tr("no_compare") + '</span>';
    if (c.previous && prevTiny() && fmtPrev) return '<span class="d none">' + tr("prev_value", { v: fmtPrev(c.previous) }) + '</span>';
    if (POINTS[key]) {
      const d = c.current - c.previous;
      if (Math.abs(d) < 0.5) return '<span class="d same">' + tr("same") + '</span>';
      return `<span class="d ${judge ? (d > 0 ? "up" : "down") : "neutral"}">${d > 0 ? "↑" : "↓"} ${fmt(Math.abs(d), 0)} ${tr("pts")}</span>`;
    }
    if (!c.previous) return c.current ? '<span class="d none">' + tr("nothing_before") + '</span>' : '<span class="d none">–</span>';
    const p = c.delta_pct;
    if (p == null || p === 0) return '<span class="d same">' + tr("same") + '</span>';
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
    if (dash.period === "all") {
      delta = "";
    } else if (d.previous && !cur.rides) {
      delta = tr("hero_prev", { versus: dash.versus.replace(tr("versus_prefix"), ""), km: fmt(d.previous, 1) });
    } else if (d.previous && prevTiny()) {
      delta = `<span class="d none">${tr("prev_value", { v: fmt(d.previous, 1) + " km" })}</span>`;
    } else if (d.previous) {
      const p = d.delta_pct || 0;
      const cls = p > 0 ? "up" : p < 0 ? "down" : "same";
      delta = tr("hero_vs", { delta: `<span class="d ${cls}">${change(d)}</span>`, versus: dash.versus, km: fmt(d.previous, 1) });
    } else if (cur.distance_km) {
      delta = tr("hero_none_before");
    }
    $("hero-delta").innerHTML = delta;

    const energy = (cur.rider_kj || 0) + (cur.motor_kj || 0);
    const split = $("hero-split");
    split.hidden = !energy || !cur.rides;
    if (energy) {
      const rp = 100 * cur.rider_kj / energy;
      split.querySelector(".rider").style.width = rp + "%";
      split.querySelector(".motor").style.width = (100 - rp) + "%";
      const mwh = cur.motor_wh != null ? cur.motor_wh : (cur.motor_kj || 0) / 3.6;
      $("split-rider").innerHTML = `<i></i>${tr("you")} ${fmt(rp, 0)} % · ${fmt(cur.rider_wh, 0)} Wh`;
      $("split-motor").innerHTML = `<i></i>${tr("motor")} ${fmt(100 - rp, 0)} % · ${fmt(mwh, 0)} Wh`;
      $("split-rider").title = fmt(cur.rider_kj, 0) + " kJ";
      $("split-motor").title = fmt(cur.motor_kj, 0) + " kJ";
    }
    const empty = $("hero-empty");
    empty.hidden = cur.rides > 0;
    empty.textContent = "";
    if (!cur.rides) {
      empty.appendChild(document.createTextNode(tr("no_rides_period")));
      if (dash.last_offset != null && dash.last_offset !== dash.offset) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn ghost small-btn";
        b.textContent = tr("jump_last");
        b.addEventListener("click", () => load(dash.period, dash.last_offset));
        empty.appendChild(b);
      }
    }

    $("kpis").hidden = !cur.rides;
    $("kpis").innerHTML = KPIS.map(([key, label, f, cls, judge]) => {
      const v = cur[key];
      const tip = key === "rider_wh" && cur.rider_kj != null ? ` title="${esc(fmt(cur.rider_kj, 0) + " kJ")}"` : "";
      return `<div class="kpi ${cls}"${tip}><div class="v">${v == null ? "–" : f(v)}</div><div class="k">${label}</div>${deltaHtml(key, cmp[key], judge, (pv) => f(pv))}</div>`;
    }).join("");

    const sum = (k) => dash.series.reduce((a, s) => a + (s[k] || 0), 0);
    const span = tr("span." + dash.period, { n: dash.series.length });
    $("sum-distance").textContent = tr("sum_in", { v: fmt(sum("distance_km"), 0), unit: "km", span });
    $("sum-ascent").textContent = tr("sum_in", { v: fmt(sum("ascent_m"), 0), unit: "m", span });
    renderMix();
    renderCompare();
    markNew();
  }

  function renderCompare() {
    const box = $("compare-by");
    if (!box) return;
    const show = dash.compare_mode != null && (!!dash.compare_partial ||
      (dash.offset === 0 && ["week", "month", "year"].includes(dash.period)));
    box.hidden = !show;
    const until = deDate(dash.previous_until);
    box.querySelectorAll("button[data-key]").forEach((b) => {
      b.setAttribute("aria-pressed", b.dataset.key === dash.compare_mode ? "true" : "false");
      b.title = b.dataset.key === "elapsed"
        ? (until ? tr("cmp.elapsed_tip", { d: until }) : tr("cmp.elapsed"))
        : tr("cmp.full_tip");
    });
  }

  // Bestwert aus dem angezeigten Zeitraum: Kachel markieren
  function markNew() {
    const cur = dash.current || {};
    const cs = day10(cur.start), ce = day10(cur.end);
    document.querySelectorAll("article.best").forEach((el) => {
      const bs = day10(el.dataset.start), be = day10(el.dataset.end);
      const isNew = dash.period !== "all" && !!(cs && ce && bs && be && bs >= cs && be <= ce);
      el.classList.toggle("is-new", isNew);
      let chip = el.querySelector(".best-new");
      if (isNew && !chip) {
        chip = document.createElement("span");
        chip.className = "best-new";
        chip.textContent = tr("best.new");
        chip.title = tr("best.new_tip");
        el.appendChild(chip);
      } else if (!isNew && chip) {
        chip.remove();
      }
    });
  }

  /* --------------------------------------------------------- Verteilung */

  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // wie Jinja-Filter „duration“: m:ss bzw. h:mm:ss
  function dur(sec) {
    if (sec == null || !isFinite(sec)) return "–";
    const s = Math.round(sec), h = Math.floor(s / 3600), rest = s % 3600;
    const mm = Math.floor(rest / 60), ss = rest % 60;
    return h ? h + ":" + String(mm).padStart(2, "0") + ":" + String(ss).padStart(2, "0") : mm + ":" + String(ss).padStart(2, "0");
  }

  // unit: Einheit statt Dauer (Wert per fmt mit digits Nachkommastellen)
  function barsHtml(data, cls, dropZero, labels, unit, digits) {
    const entries = Object.entries(data || {}).filter(([k, v]) => typeof v === "number" && !(dropZero && k === "<1"));
    const total = entries.reduce((a, [, v]) => a + v, 0);
    const peak = entries.reduce((a, [, v]) => Math.max(a, v), 0);
    const rows = entries.filter(([, v]) => v).map(([k, v]) =>
      `<div class="bar"><span class="label" title="${esc(k)}">${esc((labels && labels[k]) || k)}</span><span class="track"><span class="fill ${cls || ""}" style="width: ${peak ? 100 * v / peak : 0}%"></span></span><span class="val">${unit ? fmt(v, digits) + " " + esc(unit) : dur(v)}</span></div>`).join("");
    return `<div class="bars${unit ? " unit" : ""}">${rows}${total ? "" : '<p class="muted small">' + esc(tr("mix.no_data")) + "</p>"}</div>`;
  }

  // zwei dünne Balken je Zeile (a/b), gemeinsame Skala – wie Jinja-Makro bars_pair
  function barsPairHtml(data, keyA, keyB, clsA, clsB, unit, labels) {
    const entries = Object.entries(data || {});
    const peak = entries.reduce((a, [, r]) => Math.max(a, r[keyA] || 0, r[keyB] || 0), 0);
    const w = (v) => (peak ? 100 * (v || 0) / peak : 0);
    const rows = entries.map(([k, r]) =>
      `<div class="bar"><span class="label" title="${esc(k)}">${esc((labels && labels[k]) || k)}</span><span class="track-pair"><span class="track"><span class="fill ${clsA}" style="width: ${w(r[keyA])}%"></span></span><span class="track"><span class="fill ${clsB}" style="width: ${w(r[keyB])}%"></span></span></span><span class="val">${fmt(r[keyA], 0)} / ${fmt(r[keyB], 0)} ${esc(unit)}</span></div>`).join("");
    return `<div class="bars pair">${rows}${peak ? "" : '<p class="muted small">' + esc(tr("mix.no_data")) + "</p>"}</div>`;
  }

  function renderMix() {
    const section = $("mix-section"), box = $("mix");
    const m = dash.mix;
    if (!section || !box) return;
    if (!m || !m.rides) { section.hidden = true; box.innerHTML = ""; return; }
    section.hidden = false;
    const a = m.assist || {}, g = m.gears || {}, d = m.distributions || {};
    const modeRows = Object.entries(a.per_mode || {}).map(([name, x]) =>
      `<tr${(x.distance_m || 0) < 500 ? ' class="muted"' : ""}><td>${esc(name)}</td><td class="n">${fmt((x.distance_m || 0) / 1000, 1)} km</td><td class="n">${fmt(x.avg_moving_speed_kmh, 1)}</td><td class="n">${fmt(x.avg_motor_power_w, 0)} W</td><td class="n">${fmt(x.avg_rider_power_w, 0)} W</td></tr>`).join("");
    const card = (title, sub, data, cls, dropZero, labels) =>
      `<section class="card"><h2>${esc(title)}${sub ? ` <span class="muted small">${esc(sub)}</span>` : ""}</h2>${barsHtml(data, cls, dropZero, labels)}</section>`;
    // Steigungsbereiche lesbar: ↓ bergab, ↑ bergauf
    const gradLabels = { "<-10": "↓ >10 %", "-10–-6": "↓ 6–10 %", "-6–-3": "↓ 3–6 %", "-3–-1": "↓ 1–3 %", "-1–1": tr("mix.d.flat"), "1–3": "↑ 1–3 %", "3–6": "↑ 3–6 %", "6–10": "↑ 6–10 %", "≥10": "↑ >10 %" };
    const powerCards = [
      card(tr("mix.d.rider"), tr("mix.in_motion"), d.rider_power_s, "rider", true),
      card(tr("mix.d.motor"), tr("mix.in_motion"), d.motor_power_s, "motor", true),
      card(tr("mix.d.cadence"), tr("mix.cadence_moving"), d.cadence_s, "rider", true),
      card(tr("mix.d.speed"), tr("mix.speed_moving"), d.speed_s, "", true),
    ];
    if (d.heart_rate_s) powerCards.push(card(tr("mix.d.hr"), "bpm", d.heart_rate_s, "hr"));
    const bg = m.by_gradient || {};
    const spdGrad = {};
    Object.entries(bg).forEach(([k, r]) => { spdGrad[k] = r.avg_speed_kmh; });
    const hillCards = [
      card(tr("mix.d.gradient"), tr("mix.d.grad_hint"), d.gradient_s, "", false, gradLabels),
      `<section class="card"><h2>${esc(tr("mix.d.power_grad"))} <span class="muted small">${esc(tr("mix.d.power_grad_sub"))}</span></h2>${barsPairHtml(bg, "avg_rider_power_w", "avg_motor_power_w", "rider", "motor", "W", gradLabels)}<p class="muted small" style="margin-top:0.4rem"><span style="color:var(--rider)">&#9632;</span> ${esc(tr("mix.d.legend_rider"))} &nbsp; <span style="color:var(--motor)">&#9632;</span> ${esc(tr("mix.d.legend_motor"))}</p></section>`,
      `<section class="card"><h2>${esc(tr("mix.d.speed_grad"))} <span class="muted small">${esc(tr("mix.d.speed_grad_sub"))}</span></h2>${barsHtml(spdGrad, "", false, gradLabels, "km/h", 1)}</section>`,
    ];
    const cadGear = g.avg_cadence_per_gear || {};
    const cadGearCard = Object.keys(cadGear).length ? `<section class="card"><h2>${esc(tr("mix.d.cad_gear"))} <span class="muted small">${esc(tr("mix.d.cad_gear_sub"))}</span></h2>${barsHtml(cadGear, "rider", false, null, tr("mix.d.rpm"), 0)}</section>` : "";
    const hasCad = Object.keys(cadGear).length > 0;
    const aiCard = (key) => `<section class="card ai-card ai-${key}" data-ai="${key}" hidden><div class="ai-body"></div></section>`;
    const grp = (cls, key, inner, t) => `<div class="${t}"><h3 class="group-title ${cls}">${esc(tr(key))}</h3>${inner}</div>`;
    box.innerHTML =
      grp("g-motor", "mix.sec.drive", `<div class="grid cols-drive${hasCad ? "" : " two"}">
        <section class="card"><div class="chart-head"><h2>${esc(tr("mix.modes"))} <span class="muted small">${esc(tr("mix.while_moving"))}</span></h2><span class="muted small">${esc(tr("mix.changes", { n: fmt(a.changes || 0, 0) }))}</span></div>${barsHtml(a.moving_seconds_per_mode || a.seconds_per_mode, "motor")}
          <table class="plain" style="margin-top:0.9rem"><tr><th>${esc(tr("mix.th.mode"))}</th><th class="n">${esc(tr("mix.th.distance"))}</th><th class="n">${esc(tr("mix.th.avg_kmh"))}</th><th class="n">${esc(tr("mix.th.avg_motor"))}</th><th class="n">${esc(tr("mix.th.avg_rider"))}</th></tr>${modeRows}</table></section>
        <section class="card"><div class="chart-head"><h2>${esc(tr("mix.gears"))} <span class="muted small">${esc(tr("mix.while_moving"))}</span></h2><span class="muted small">${esc(tr("mix.gear_changes", { n: fmt(g.changes || 0, 0) }))}</span></div>${barsHtml(g.moving_seconds_per_gear || g.seconds_per_gear, "")}</section>
        ${cadGearCard}
        ${aiCard("drive")}
      </div>`, "t-motor") +
      grp("g-rider", "mix.sec.power", `<div class="grid cols-3 cols-power${d.heart_rate_s ? " has-hr" : ""}">${powerCards.join("")}${aiCard("power")}</div>`, "t-rider") +
      grp("g-ride", "mix.sec.hill", `<div class="grid cols-3">${hillCards.join("")}${aiCard("hill")}</div>`, "t-hill");
    if (window.avinoxAi) window.avinoxAi.apply();
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
      series: [{ label: tr("period"), value: (u, v) => (v == null ? "" : titles[v] || "") }].concat(cfg.series),
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
      const parts = cfg.rows.map((row) => {
        const v = row.values[i];
        const txt = row.value ? row.value(null, v, 0, i) : fmt(v, 0);
        return `<i style="background:${row.color}"></i>${row.label} <b>${txt}</b>`;
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
      if (dash.period === "all") { load("year", n - 1 - i); return; }
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
      document.querySelectorAll(".dchart").forEach((el) => { el.innerHTML = '<p class="muted small">' + tr("charts_failed") + '</p>'; });
      return;
    }
    const c = colors();
    const s = dash.series;
    const val = (unit, digits) => (u, v) => (v == null ? "–" : fmt(v, digits) + " " + unit);
    const anyRide = s.some((x) => x.rides > 0);
    const open = s.map(isOpen);
    // Balken: abgeschlossene Zeiträume voll, laufender heller mit gestrichelter Kante
    const split = (arr) => [arr.map((v, i) => (open[i] ? null : v)), arr.map((v, i) => (open[i] ? v : null))];
    const barPair = (label, color, arr, size, align, value) => {
      const common = { stroke: color, points: { show: false }, paths: bars(size, align), value };
      return {
        data: split(arr),
        series: [Object.assign({ label, fill: color, width: 0 }, common),
                 Object.assign({ label, fill: withAlpha(color, 0.45), width: 1.5, dash: [4, 4] }, common)],
        row: { label, color, values: arr, value },
      };
    };
    const compose = (parts, extra) => Object.assign({
      data: parts.reduce((acc, p) => acc.concat(p.data), []),
      series: parts.reduce((acc, p) => acc.concat(p.series), []),
      rows: parts.map((p) => p.row),
    }, extra || {});
    const wideBar = (label, color, arr, unit, digits) => barPair(label, color, arr, [0.62, 40], 0, val(unit, digits));
    const energyVal = (key) => (u, v, si, i) => (v == null ? "–"
      : fmt(v, 0) + " Wh (" + fmt(s[i] && s[i][key], 0) + " kJ)");
    const motorWh = s.map((x) => (x.motor_wh != null ? x.motor_wh : (x.motor_kj || 0) / 3.6));
    const shareVals = s.map((x) => x.motor_share_pct);
    const lastOpen = n1 => n1 >= 1 && open[n1];
    const last = s.length - 1;
    const dashedLast = lastOpen(last) && last >= 1;
    const shareMain = dashedLast ? shareVals.map((v, i) => (i === last ? null : v)) : shareVals;
    const shareTail = dashedLast ? shareVals.map((v, i) => (i >= last - 1 ? v : null)) : null;
    const shareSeries = [{ label: tr("s.share"), stroke: c.motor, width: 2, spanGaps: true,
      points: { show: true, size: 6, fill: c.motor }, value: val("%", 0) }];
    const shareData = [shareMain];
    if (shareTail) {
      shareSeries.push({ label: tr("s.share"), stroke: c.motor, width: 2, dash: [4, 4], spanGaps: false,
        points: { show: true, size: 6, fill: cssVar("--surface", "#fff"), stroke: c.motor, filter: () => [last] }, value: val("%", 0) });
      shareData.push(shareTail);
    }
    const defs = [
      ["chart-distance", compose([wideBar(tr("s.distance"), c.alt, s.map((x) => x.distance_km), "km", 1)])],
      ["chart-energy", compose([
        barPair(tr("s.rider"), c.rider, s.map((x) => x.rider_wh), [0.36, 22], -1, energyVal("rider_kj")),
        barPair(tr("s.motor"), c.motor, motorWh, [0.36, 22], 1, energyVal("motor_kj")),
      ])],
      ["chart-share", {
        data: shareData,
        yRange: [0, 100], unitTick: " %", ySplits: () => [0, 25, 50, 75, 100],
        series: shareSeries,
        rows: [{ label: tr("s.share"), color: c.motor, values: shareVals, value: val("%", 0) }],
      }],
      ["chart-ascent", compose([wideBar(tr("s.ascent"), c.alt, s.map((x) => x.ascent_m), "m", 0)])],
    ];
    defs.forEach(([id, cfg]) => {
      const el = $(id);
      if (!el) return;
      el.textContent = "";
      if (!anyRide) {
        el.innerHTML = '<div class="chart-empty">' + tr("no_rides_chart") + '</div>';
        return;
      }
      cfg.readout = $(id.replace("chart-", "readout-"));
      if (cfg.readout) cfg.readout.textContent = "";
      try { charts.push(makeChart(el, cfg)); } catch (e) { el.innerHTML = '<p class="muted small">' + tr("chart_unavailable") + '</p>'; }
    });
  }

  /* --------------------------------------------------------- Laden */

  function loadAi() {
    if (!window.avinoxAi) return;
    window.avinoxAi.watch(`/api/v1/ai/dashboard?period=${encodeURIComponent(dash.period)}&offset=${dash.offset || 0}&compare=${cmp}&lang=${window.avinoxAi.lang}`);
  }

  let pending = null;
  function load(period, offset) {
    const url = `/api/v1/dashboard?period=${encodeURIComponent(period)}&offset=${offset}&compare=${cmp}`;
    const token = pending = {};
    $("period").classList.add("loading");
    fetch(url, { credentials: "same-origin" })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((d) => {
        if (token !== pending) return;
        dash = normalize(d);
        const q = new URLSearchParams(location.search);
        q.set("period", d.period);
        if (d.offset) q.set("offset", d.offset); else q.delete("offset");
        if (cmp !== "elapsed") q.set("compare", cmp); else q.delete("compare");
        history.replaceState(null, "", location.pathname + "?" + q.toString());
        renderPeriod();
        buildCharts();
        loadAi();
      })
      .catch(() => {})
      .finally(() => { if (token === pending) $("period").classList.remove("loading"); });
  }

  document.querySelectorAll("#period-by button[data-key]").forEach((b) =>
    b.addEventListener("click", () => { if (b.dataset.key !== dash.period) load(b.dataset.key, 0); }));
  document.querySelectorAll("#compare-by button[data-key]").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.dataset.key === cmp) return;
      cmp = b.dataset.key;
      try { localStorage.setItem(CMP_KEY, cmp); } catch (e) { /* ohne Speicher */ }
      load(dash.period, dash.offset);
    }));
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
  document.addEventListener("themechange", buildCharts);
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    (mq.addEventListener ? mq.addEventListener.bind(mq, "change") : mq.addListener.bind(mq))(buildCharts);
  }

  renderPeriod();
  buildCharts();
  loadAi();
  // gespeicherte Wahl abweichend vom gerenderten Stand: einmal neu laden
  if (dash.compare_mode != null && dash.compare_mode !== cmp) load(dash.period, dash.offset);
})();
