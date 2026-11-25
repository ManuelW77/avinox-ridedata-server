/* Fahrphysik: Karte auf der Fahrtseite und Vergleichsseite (Tabelle plus Verlauf mit uPlot). */
(function () {
  "use strict";
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key, vars) => String(I[key] != null ? I[key] : key).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m));
  const LOCALE = I.lang === "en" ? "en-US" : "de-DE";
  const fmt = (v, d) => (v == null || !isFinite(v)) ? "–"
    : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 });
  const SETTINGS = "/settings#sec-phys";

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function tip(text, title) { const s = el("span", "ph-tip", text); s.title = title; return s; }
  function figure(label, value, unit, extra) {
    const f = el("div", "figure");
    const v = el("div", "v", value);
    if (unit) v.appendChild(el("small", null, unit));
    const k = el("div", "k");
    if (label instanceof Node) k.appendChild(label); else k.textContent = label;
    f.append(v, k);
    if (extra) f.appendChild(extra);
    return f;
  }
  function settingsLink(text) { const a = el("a", null, text); a.href = SETTINGS; return a; }
  const word = (prefix, code) => (I[prefix + code] != null ? I[prefix + code] : null);

  async function getJson(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" });
    let body = null;
    try { body = await r.json(); } catch (e) { /* kein JSON */ }
    return { ok: r.ok, status: r.status, body };
  }
  function massMissing(root) {
    root.textContent = "";
    const p = el("p", "notice", tr("ph.mass_missing") + " ");
    p.appendChild(settingsLink(tr("ph.mass_link")));
    root.appendChild(p);
  }
  function failed(root) { root.textContent = ""; root.appendChild(el("p", "notice", tr("ph.error"))); }

  // ------------------------------------------------------------ Fahrtseite
  function reasonLine(code, withSettings) {
    const p = el("p", "muted small", word("ph.r.", code) || code);
    if (withSettings) { p.append(" "); p.appendChild(settingsLink(tr("ph.settings_link"))); }
    return p;
  }

  function section(title) { const s = el("div"); s.appendChild(el("h3", null, title)); return s; }

  function renderRide(root, d) {
    root.textContent = "";
    const note = el("p", "muted small", tr("ph.note"));
    const res = d.resistance;

    // Rollen und Luft
    const top = section(tr("ph.roll"));
    if (res) {
      const g = el("div", "ph-grid");
      const crrTxt = fmt(res.crr, 4);
      const range = res.crr_p10 != null && res.crr_p90 != null
        ? el("div", "muted small", tr("ph.roll_range", { lo: fmt(res.crr_p10, 4), hi: fmt(res.crr_p90, 4) })) : null;
      g.appendChild(figure(tip(tr("ph.roll"), tr("ph.roll_tip")), crrTxt, "", range));
      g.lastChild.querySelector(".k").appendChild(el("small", null, " · " + tr("ph.estimate")));
      const src = res.cda_source === "fitted" ? tr("ph.cda_fitted") : tr("ph.cda_assumed");
      const cdaK = el("span"); cdaK.appendChild(tip(tr("ph.cda"), tr("ph.cda_tip"))); cdaK.appendChild(el("small", null, " · " + src));
      g.appendChild(figure(cdaK, fmt(res.cda, 2), "m²"));
      top.appendChild(g);
      root.appendChild(top);

      const flat = section(tr("ph.flat"));
      flat.appendChild(el("p", "muted small", tr("ph.flat_sub")));
      const row = el("div", "ph-flat");
      ["20", "25"].forEach((kmh) => {
        const f = res.flat_power_w && res.flat_power_w[kmh];
        if (!f) return;
        const box = el("div");
        box.appendChild(figure(tr("ph.flat_at", { v: kmh }), fmt(f.w), "W"));
        const bar = el("div", "ph-stack");
        const total = (f.roll_w + f.air_w) || 1;
        const a = el("i", "roll"); a.style.width = (100 * f.roll_w / total) + "%";
        const b = el("i", "air"); b.style.width = (100 * f.air_w / total) + "%";
        bar.append(a, b);
        const lg = el("div", "ph-legend");
        const l1 = el("span"); l1.appendChild(el("i")); l1.firstChild.style.background = "var(--alt)"; l1.append(tr("ph.flat_roll") + " " + fmt(f.roll_w) + " W");
        const l2 = el("span"); l2.appendChild(el("i")); l2.firstChild.style.background = "var(--motor)"; l2.append(tr("ph.flat_air") + " " + fmt(f.air_w) + " W");
        lg.append(l1, l2);
        box.append(bar, lg);
        row.appendChild(box);
      });
      flat.appendChild(row);
      root.appendChild(flat);
    } else {
      top.appendChild(reasonLine(d.resistance_reason));
      root.appendChild(top);
    }

    // Energie
    const en = section(tr("ph.energy"));
    if (d.energy) {
      const e = d.energy, g = el("div", "ph-grid");
      g.appendChild(figure(tr("ph.e_total"), fmt(e.wh_per_km, 1), "Wh/km"));
      g.appendChild(figure(tr("ph.e_rider"), fmt(e.rider_wh_per_km, 1), "Wh/km"));
      g.appendChild(figure(tr("ph.e_motor"), fmt(e.motor_wh_per_km, 1), "Wh/km"));
      g.appendChild(figure(tr("ph.e_share"), fmt(e.motor_share_pct, 0), "%"));
      if (e.battery) {
        const f = figure(tip(tr("ph.e_battery"), tr("ph.e_battery_tip")), fmt(e.battery.motor_battery_efficiency_pct, 0), "%");
        f.querySelector(".k").appendChild(el("small", null, " · " + tr("ph.estimate")));
        g.appendChild(f);
      }
      en.appendChild(g);
      if (!e.battery && e.battery_reason) en.appendChild(reasonLine(e.battery_reason, e.battery_reason === "battery_capacity_missing"));
    } else {
      en.appendChild(reasonLine(d.energy_reason));
    }
    root.appendChild(en);

    // Bergauf
    const cl = section(tr("ph.climb"));
    if (d.climbing) {
      const c = d.climbing, g = el("div", "ph-grid");
      g.appendChild(figure(tr("ph.c_total"), fmt(c.total_w_per_kg, 2), "W/kg"));
      g.appendChild(figure(tr("ph.c_rider"), fmt(c.rider_w_per_kg, 2), "W/kg"));
      g.appendChild(figure(tr("ph.c_vam"), fmt(c.vam_m_per_h), "m/h"));
      cl.appendChild(g);
      const lv = (c.assist || []).filter((x) => x.factor != null);
      if (lv.length) {
        cl.appendChild(el("h3", null, tr("ph.c_assist")));
        const wrap = el("div", "ph-scroll"), tbl = el("table", "plain");
        const hr = el("tr");
        [tr("ph.th_level"), tr("ph.th_time"), null].forEach((h, i) => {
          const th = el("th", i ? "n" : null);
          if (h) th.textContent = h; else th.appendChild(tip(tr("ph.th_factor"), tr("ph.factor_tip")));
          hr.appendChild(th);
        });
        const thead = el("thead"); thead.appendChild(hr); tbl.appendChild(thead);
        const tb = el("tbody");
        lv.forEach((x) => {
          const tr_ = el("tr");
          tr_.appendChild(el("td", null, x.name));
          tr_.appendChild(el("td", "n", fmt(Math.round(x.seconds / 60)) + " min"));
          tr_.appendChild(el("td", "n", "×" + fmt(x.factor, 2)));
          tb.appendChild(tr_);
        });
        tbl.appendChild(tb); wrap.appendChild(tbl); cl.appendChild(wrap);
      }
    } else {
      cl.appendChild(reasonLine(d.climbing_reason));
    }
    root.appendChild(cl);

    // Bedingungen
    const co = d.conditions;
    if (co) {
      const sec = section(tr("ph.cond"));
      const g = el("div", "ph-grid");
      if (co.avg_temp_c != null) g.appendChild(figure(tr("ph.temp"), fmt(co.avg_temp_c, 1), "°C"));
      const ob = co.out_and_back;
      if (ob && ob.detected) {
        const hdr = el("div", "k"); hdr.appendChild(tip(tr("ph.ob"), tr("ph.ob_tip")));
        const box = el("div", "figure");
        const v = el("div", "v", fmt(ob.crr_out, 4) + " → " + fmt(ob.crr_back, 4));
        const mean = el("div", "muted small", tr("ph.ob_out") + " → " + tr("ph.ob_back") + " · " + tr("ph.ob_mean") + " " + fmt(ob.crr_mean, 4));
        box.append(v, hdr, mean);
        g.appendChild(box);
      }
      if (g.children.length) { sec.appendChild(g); root.appendChild(sec); }
    }

    // Warnungen
    const notes = [];
    (d.warnings || []).forEach((w) => { const t = word("ph.w.", w); if (t) notes.push(t); });
    if (notes.length) {
      const ul = el("ul", "ph-notes");
      notes.forEach((n) => ul.appendChild(el("li", null, n)));
      root.appendChild(ul);
    }
    root.appendChild(note);
  }

  async function initRide() {
    const root = document.getElementById("ph-card");
    if (!root) return;
    try {
      const r = await getJson("/api/v1/rides/" + encodeURIComponent(root.dataset.ride) + "/physics");
      if (r.ok) renderRide(root, r.body);
      else if (r.status === 409 && r.body && r.body.detail && r.body.detail.error === "mass_missing") massMissing(root);
      else failed(root);
    } catch (e) { failed(root); }
  }

  // ------------------------------------------------------------ Vergleich
  const cssVar = (n, f) => (getComputedStyle(document.documentElement).getPropertyValue(n) || "").trim() || f;
  let chart = null, lastData = null;

  function drawChart(host, rides) {
    if (chart) { chart.destroy(); chart = null; }
    host.textContent = "";
    const pts = rides.filter((r) => r.crr != null || r.flat_power_w_25 != null);
    if (pts.length < 2 || typeof uPlot === "undefined") return;
    const xs = pts.map((_, i) => i);
    const dayLabel = (i) => { const r = pts[i]; if (!r) return ""; const p = r.date.split("-"); return I.lang === "en" ? p[1] + "/" + p[2] : p[2] + "." + p[1] + "."; };
    const crr = pts.map((r) => r.crr), need = pts.map((r) => r.flat_power_w_25);
    const c1 = cssVar("--alt", "#6b6f9a"), c2 = cssVar("--motor", "#13877a");
    const ink = cssVar("--ink-2", "#57605f"), line = cssVar("--line", "#dcd8cf");
    const font = "11px " + cssVar("--font", "sans-serif");
    const legend = el("div", "legend");
    [[c1, tr("ph.cmp.chart_crr")], [c2, tr("ph.cmp.chart_need")]].forEach(([col, txt]) => {
      const s = el("span"); const i = el("i"); i.style.background = col; s.append(i, txt); legend.appendChild(s);
    });
    const box = el("div"); host.append(el("h3", null, tr("ph.cmp.chart")), legend, box);
    const width = Math.max(240, box.clientWidth || host.clientWidth || 500);
    const axis = (scale, stroke, side, dec) => ({
      scale, stroke, font, side, size: 52, ticks: { show: false },
      grid: side === 3 ? { show: false } : { stroke: line, width: 1 },
      values: (u, vs) => vs.map((v) => fmt(v, dec)),
    });
    const ser = (label, color, scale) => ({ label, scale, stroke: color, width: 2, spanGaps: true, points: { show: true, size: 6, fill: color } });
    chart = new uPlot({
      width, height: width < 420 ? 190 : 230, padding: [8, 8, 0, 0],
      legend: { show: false }, cursor: { drag: { x: false, y: false } },
      scales: { x: { time: false, range: (u, mn, mx) => [mn - 0.3, mx + 0.3] }, crr: { range: (u, mn, mx) => [Math.max(0, mn * 0.9), mx * 1.1 || 1] }, need: { range: (u, mn, mx) => [Math.max(0, mn * 0.9), mx * 1.1 || 1] } },
      series: [{}, ser(tr("ph.cmp.chart_crr"), c1, "crr"), ser(tr("ph.cmp.chart_need"), c2, "need")],
      axes: [{ stroke: ink, font, grid: { show: false }, ticks: { show: false }, splits: (u) => { const step = Math.max(1, Math.ceil(pts.length / Math.max(2, Math.floor(u.bbox.width / devicePixelRatio / 60)))); const o = []; for (let i = 0; i < pts.length; i += step) o.push(i); return o; }, values: (u, vs) => vs.map(dayLabel) }, axis("crr", c1, 1, 3), axis("need", c2, 3, 0)],
    }, [xs, crr, need], box);
  }

  function renderCompare(body, data) {
    body.textContent = "";
    const rides = data.rides || [];
    if (!rides.length) { body.appendChild(el("p", "muted", tr("ph.cmp.empty"))); return; }
    body.appendChild(el("p", "muted small", tr("ph.cmp.count", { n: data.count }) + " · " + tr("ph.note")));
    const wrap = el("div", "ph-scroll"), tbl = el("table", "plain");
    const heads = [["ph.cmp.date"], ["ph.cmp.ride"], ["ph.cmp.crr", "ph.roll_tip"], ["ph.cmp.need", "ph.flat_sub"], ["ph.cmp.whkm"], ["ph.cmp.share"], ["ph.cmp.wkg"], ["ph.cmp.temp"]];
    const hr = el("tr");
    heads.forEach((h, i) => {
      const th = el("th", i > 1 ? "n" : null);
      th.appendChild(h[1] ? tip(tr(h[0]), tr(h[1])) : document.createTextNode(tr(h[0])));
      hr.appendChild(th);
    });
    const thead = el("thead"); thead.appendChild(hr); tbl.appendChild(thead);
    const tb = el("tbody");
    const cell = (txt, cls) => el("td", cls || "n", txt);
    // Tabelle: neueste Fahrt oben (der Verlauf darunter bleibt chronologisch)
    rides.slice().reverse().forEach((r) => {
      const row = el("tr", r.outlier ? "outlier" : "");
      row.appendChild(cell(r.date, ""));
      const nm = el("td"); const a = el("a", null, r.name || ("#" + r.id)); a.href = "/rides/" + r.id; nm.appendChild(a);
      if (r.outlier) { const w = el("div", "small", tr("ph.cmp.outlier")); nm.appendChild(w); }
      row.appendChild(nm);
      row.appendChild(cell(fmt(r.crr, 4)));
      row.appendChild(cell(r.flat_power_w_25 != null ? fmt(r.flat_power_w_25) + " W" : "–"));
      row.appendChild(cell(fmt(r.wh_per_km, 1)));
      row.appendChild(cell(r.motor_share_pct != null ? fmt(r.motor_share_pct, 0) + " %" : "–"));
      row.appendChild(cell(fmt(r.climb_rider_w_per_kg, 2)));
      const t = cell(r.avg_temp_c != null ? fmt(r.avg_temp_c, 1) + " °C" : "–");
      if (r.temp_is_device && r.avg_temp_c != null) t.title = tr("ph.cmp.temp_dev");
      row.appendChild(t);
      tb.appendChild(row);
    });
    const m = data.median || {};
    const mr = el("tr", "median");
    mr.appendChild(cell(tr("ph.cmp.median"), "")); mr.appendChild(cell("", ""));
    mr.appendChild(cell(fmt(m.crr, 4)));
    mr.appendChild(cell(m.flat_power_w_25 != null ? fmt(m.flat_power_w_25) + " W" : "–"));
    mr.appendChild(cell(fmt(m.wh_per_km, 1)));
    mr.appendChild(cell(m.motor_share_pct != null ? fmt(m.motor_share_pct, 0) + " %" : "–"));
    mr.appendChild(cell(fmt(m.climb_rider_w_per_kg, 2)));
    mr.appendChild(cell(m.avg_temp_c != null ? fmt(m.avg_temp_c, 1) + " °C" : "–"));
    tb.appendChild(mr);
    tbl.appendChild(tb); wrap.appendChild(tbl); body.appendChild(wrap);
    const host = el("div", "ph-chart"); body.appendChild(host);
    drawChart(host, rides);
  }

  function initCompare() {
    const body = document.getElementById("ph-cmp-body");
    if (!body) return;
    const from = document.getElementById("ph-from"), to = document.getElementById("ph-to");
    const group = document.getElementById("ph-range");
    const iso = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    async function load() {
      body.textContent = ""; body.appendChild(el("p", "muted small", tr("ph.loading")));
      const q = new URLSearchParams();
      if (from.value) q.set("start", from.value);
      if (to.value) q.set("end", to.value);
      try {
        const r = await getJson("/api/v1/physics/compare" + (q.toString() ? "?" + q : ""));
        if (r.ok) { lastData = r.body; renderCompare(body, r.body); }
        else if (r.status === 409) massMissing(body);
        else failed(body);
      } catch (e) { failed(body); }
    }
    function press(key) { group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.key === key ? "true" : "false")); }
    group.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-key]");
      if (!b) return;
      press(b.dataset.key);
      if (b.dataset.key === "all") { from.value = ""; to.value = ""; }
      else { const d = new Date(); to.value = iso(d); d.setDate(d.getDate() - Number(b.dataset.key)); from.value = iso(d); }
      load();
    });
    [from, to].forEach((i) => i.addEventListener("change", () => { press(""); load(); }));
    document.addEventListener("themechange", () => { if (lastData) renderCompare(body, lastData); });
    load();
  }

  initRide();
  initCompare();
})();
