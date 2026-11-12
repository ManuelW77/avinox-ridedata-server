/* Fahrtansicht: Karte (Leaflet) und Verlaufsdiagramme (uPlot), gekoppelt über die Strecke. */
(function () {
  "use strict";

  const dataEl = document.getElementById("ride-data");
  if (!dataEl) return;
  // Texte aus <script id="i18n"> (siehe base.html), Sprache in I.lang
  let I = {};
  try { I = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { I = {}; }
  const tr = (key, vars) => String(I[key] != null ? I[key] : key).replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? vars[k] : m));
  const LOCALE = I.lang === "en" ? "en-US" : "de-DE";
  let ride;
  try { ride = JSON.parse(dataEl.textContent); } catch (e) { return; }

  const series = ride.series || { fields: [], rows: [] };
  const fields = series.fields || [];
  const rows = (series.rows || []).filter(Array.isArray);
  const col = (name) => fields.indexOf(name);
  const modes = ride.modes || {};

  const css = () => getComputedStyle(document.documentElement);
  const cssVar = (name, fallback) => (css().getPropertyValue(name) || "").trim() || fallback;

  const fmt = (v, digits) => (v == null || !isFinite(v)) ? "–"
    : Number(v).toLocaleString(LOCALE, { minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0 });

  function withAlpha(color, a) {
    const c = color.trim();
    let m = /^#([0-9a-f]{3})$/i.exec(c);
    if (m) {
      const h = m[1];
      return `rgba(${parseInt(h[0] + h[0], 16)},${parseInt(h[1] + h[1], 16)},${parseInt(h[2] + h[2], 16)},${a})`;
    }
    m = /^#([0-9a-f]{6})$/i.exec(c);
    if (m) {
      const n = parseInt(m[1], 16);
      return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    }
    return c;
  }

  function hmm(sec) {
    if (sec == null || !isFinite(sec)) return "";
    const s = Math.round(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return h + ":" + String(m).padStart(2, "0");
  }

  function modeName(v) {
    if (v == null) return "–";
    const k = String(Math.round(v));
    return modes[k] || tr("mode_n", { n: k });
  }

  // Binäre Suche: Index des Werts in arr (aufsteigend, über get) am nächsten an target.
  function nearestIndex(n, get, target) {
    if (n === 0 || target == null) return -1;
    let lo = 0, hi = n - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const v = get(mid);
      if (v == null || v < target) lo = mid + 1; else hi = mid;
    }
    if (lo > 0) {
      const a = get(lo - 1), b = get(lo);
      if (a != null && (b == null || Math.abs(a - target) <= Math.abs(b - target))) return lo - 1;
    }
    return lo;
  }

  function setPressed(group, key) {
    group.querySelectorAll("button[data-key]").forEach((b) =>
      b.setAttribute("aria-pressed", b.dataset.key === key ? "true" : "false"));
  }

  function pressedKey(group, fallback) {
    if (!group) return fallback;
    const b = group.querySelector('button[aria-pressed="true"]');
    return (b && b.dataset.key) || fallback;
  }

  /* ---------------------------------------------------------------- Karte */

  const mapState = { map: null, track: null, layer: null, marker: null, onHoverDistance: null };

  const SCALE = ["#2c7bb6", "#7fbfd4", "#e8d86a", "#f08a3e", "#c62e1d"];
  const MODE_COLORS = ["#8a908c", "#3a9a5b", "#2c7bb6", "#c62e1d", "#8e4fb3", "#e0a21a", "#13877a", "#d9661a", "#5b6abf", "#b0417a"];

  const COLOR_KEYS = {
    speed: { field: "speed_kmh", unit: "km/h", digits: 1 },
    rider: { field: "rider_power_w", unit: "W", digits: 0 },
    motor: { field: "motor_power_w", unit: "W", digits: 0 },
    alt: { field: "altitude_m", unit: "m", digits: 0 },
    mode: { field: "assist_level" },
  };

  function quantileBreaks(values) {
    const v = values.filter((x) => x != null && isFinite(x)).sort((a, b) => a - b);
    if (!v.length) return null;
    const q = (p) => v[Math.min(v.length - 1, Math.floor(p * (v.length - 1)))];
    return { min: v[0], max: v[v.length - 1], cuts: [q(0.2), q(0.4), q(0.6), q(0.8)] };
  }

  function classOf(val, cuts) {
    if (val == null || !isFinite(val)) return -1;
    let i = 0;
    while (i < cuts.length && val > cuts[i]) i++;
    return i;
  }

  function legend(items) {
    const el = document.getElementById("map-legend");
    if (!el) return;
    el.textContent = "";
    items.forEach(([color, text]) => {
      const span = document.createElement("span");
      const i = document.createElement("i");
      i.style.background = color;
      span.appendChild(i);
      span.appendChild(document.createTextNode(text));
      el.appendChild(span);
    });
  }

  function drawTrack(key) {
    const t = mapState.track;
    if (!t || !mapState.map) return;
    if (mapState.layer) mapState.layer.remove();
    const cfg = COLOR_KEYS[key] || COLOR_KEYS.speed;
    const fi = t.fields.indexOf(cfg.field);
    const vals = t.samples.map((s) => (s && fi >= 0 ? s[fi] : null));
    const grey = cssVar("--ink-3", "#8a908c");
    let colorFor, items = [];

    if (key === "mode") {
      const seen = new Map();
      colorFor = (v) => {
        if (v == null) return -1;
        const k = Math.round(v);
        if (!seen.has(k)) seen.set(k, MODE_COLORS[((k % MODE_COLORS.length) + MODE_COLORS.length) % MODE_COLORS.length]);
        return k;
      };
      vals.forEach(colorFor);
      [...seen.keys()].sort((a, b) => a - b).forEach((k) => items.push([seen.get(k), modeName(k)]));
      const palette = (c) => (c === -1 ? grey : seen.get(c));
      buildSegments(t, vals.map(colorFor), palette);
    } else {
      const br = quantileBreaks(vals);
      if (br) {
        const edges = [br.min, ...br.cuts, br.max];
        for (let i = 0; i < 5; i++) {
          if (i > 0 && edges[i] === edges[i + 1] && edges[i] === edges[i - 1]) continue;
          items.push([SCALE[i], fmt(edges[i], cfg.digits) + "–" + fmt(edges[i + 1], cfg.digits) + " " + cfg.unit]);
        }
        buildSegments(t, vals.map((v) => classOf(v, br.cuts)), (c) => (c === -1 ? grey : SCALE[c]));
      } else {
        buildSegments(t, vals.map(() => -1), () => grey);
        items.push([grey, tr("no_values")]);
      }
    }
    legend(items);
  }

  function buildSegments(t, classes, palette) {
    const group = L.layerGroup();
    let start = 0;
    const n = t.latlngs.length;
    for (let i = 1; i <= n; i++) {
      if (i === n || classes[i] !== classes[start]) {
        const pts = t.latlngs.slice(start, Math.min(i + 1, n));
        if (pts.length > 1) {
          L.polyline(pts, { color: palette(classes[start]), weight: 4, opacity: 0.95, interactive: false }).addTo(group);
        }
        start = i;
      }
    }
    group.addTo(mapState.map);
    mapState.layer = group;
  }

  function initMap(geo) {
    const el = document.getElementById("map");
    if (!el || typeof L === "undefined") return;
    const map = L.map(el, { zoomControl: true, attributionControl: true });
    mapState.map = map;
    // Ausschnitt zuerst setzen: Linien vor dem ersten Ausschnitt scheitern in Leaflet (Renderer ohne Grenzen).
    const initial = [];
    (geo && Array.isArray(geo.features) ? geo.features : []).forEach((f) => {
      const g = f && f.geometry;
      if (g && g.type === "LineString") g.coordinates.forEach((c) => initial.push([c[1], c[0]]));
      if (g && g.type === "Point") initial.push([g.coordinates[1], g.coordinates[0]]);
    });
    if (!initial.length && Array.isArray(ride.preview)) ride.preview.forEach((p) => initial.push(p));
    if (initial.length) map.fitBounds(L.latLngBounds(initial), { padding: [20, 20] });
    else map.setView([0, 0], 2);
    if (ride.tile_url) {
      L.tileLayer(ride.tile_url, { attribution: ride.tile_attribution || "", maxZoom: 19 }).addTo(map);
    }
    const feats = (geo && Array.isArray(geo.features)) ? geo.features : [];
    const findKind = (kind, type) => feats.find((f) => f && f.properties && f.properties.kind === kind
      && f.geometry && f.geometry.type === type);
    const trackF = findKind("track", "LineString");
    const ink = cssVar("--ink", "#1c2226");
    const surface = cssVar("--surface", "#fffdf9");
    let bounds = null;

    if (trackF && trackF.geometry.coordinates.length > 1) {
      const p = trackF.properties;
      const coords = trackF.geometry.coordinates;
      const samples = Array.isArray(p.samples) ? p.samples : [];
      const tf = Array.isArray(p.fields) ? p.fields : [];
      mapState.track = {
        latlngs: coords.map((c) => [c[1], c[0]]),
        samples: coords.map((_, i) => samples[i] || null),
        fields: tf,
        di: tf.indexOf("distance_m"),
      };
      const colorBy = document.getElementById("color-by");
      drawTrack(pressedKey(colorBy, "speed"));
      if (colorBy) {
        colorBy.addEventListener("click", (ev) => {
          const b = ev.target.closest("button[data-key]");
          if (!b) return;
          setPressed(colorBy, b.dataset.key);
          drawTrack(b.dataset.key);
        });
      }
      bounds = L.latLngBounds(mapState.track.latlngs);

      // Hover auf der Karte -> Diagramm-Cursor
      map.on("mousemove", (ev) => {
        const t = mapState.track;
        if (!t || t.di < 0 || !mapState.onHoverDistance) return;
        const pt = map.latLngToContainerPoint(ev.latlng);
        let best = -1, bestD = Infinity;
        for (let i = 0; i < t.latlngs.length; i++) {
          const q = map.latLngToContainerPoint(t.latlngs[i]);
          const d = (q.x - pt.x) ** 2 + (q.y - pt.y) ** 2;
          if (d < bestD) { bestD = d; best = i; }
        }
        if (best >= 0 && bestD < 400) {
          const s = t.samples[best];
          if (s && s[t.di] != null) {
            showMarker(t.latlngs[best]);
            mapState.onHoverDistance(s[t.di]);
          }
        }
      });
    } else {
      const prevF = findKind("preview", "LineString");
      let pts = prevF ? prevF.geometry.coordinates.map((c) => [c[1], c[0]]) : [];
      if (pts.length < 2 && Array.isArray(ride.preview)) pts = ride.preview.filter((p) => Array.isArray(p) && p.length >= 2);
      if (pts.length > 1) {
        L.polyline(pts, { color: cssVar("--alt", "#6b6f9a"), weight: 3, dashArray: "6 6", interactive: false }).addTo(map);
        bounds = L.latLngBounds(pts);
        legend([[cssVar("--alt", "#6b6f9a"), tr("preview_nogps")]]);
      }
    }

    ["start", "end"].forEach((kind) => {
      const f = findKind(kind, "Point");
      if (!f) return;
      const c = f.geometry.coordinates;
      L.circleMarker([c[1], c[0]], {
        radius: 6, weight: 2, color: surface, fillOpacity: 1,
        fillColor: kind === "start" ? cssVar("--motor", "#13877a") : ink,
      }).bindTooltip(kind === "start" ? tr("start") : tr("end")).addTo(map);
    });

    if (geo && Array.isArray(geo.bbox) && geo.bbox.length >= 4) {
      const b = geo.bbox;
      const n = b.length >= 6 ? 3 : 2;
      bounds = L.latLngBounds([b[1], b[0]], [b[n + 1], b[n]]);
    }
    if (bounds && bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20] });
    else map.setView([0, 0], 2);
  }

  function showMarker(latlng) {
    if (!mapState.map) return;
    if (!mapState.marker) {
      mapState.marker = L.circleMarker(latlng, {
        radius: 7, weight: 3, color: cssVar("--surface", "#fffdf9"),
        fillColor: cssVar("--ink", "#1c2226"), fillOpacity: 1, interactive: false,
      }).addTo(mapState.map);
    } else {
      mapState.marker.setLatLng(latlng);
      if (!mapState.map.hasLayer(mapState.marker)) mapState.marker.addTo(mapState.map);
    }
  }

  function markerAtDistance(dist) {
    const t = mapState.track;
    if (!t || t.di < 0 || dist == null) return;
    const i = nearestIndex(t.samples.length, (k) => (t.samples[k] ? t.samples[k][t.di] : null), dist);
    if (i >= 0) showMarker(t.latlngs[i]);
  }

  function hideMarker() {
    if (mapState.marker && mapState.map) mapState.marker.remove();
  }

  /* ------------------------------------------------------------ Diagramme */

  const chartsEl = document.getElementById("charts");
  let charts = [];
  let chartRows = [];

  function buildCharts() {
    if (!chartsEl) return;
    charts.forEach((u) => u.destroy());
    charts = [];
    chartsEl.textContent = "";
    if (typeof uPlot === "undefined" || !rows.length) {
      const p = document.createElement("p");
      p.className = "muted small";
      p.textContent = rows.length ? tr("charts_failed") : tr("no_course");
      chartsEl.appendChild(p);
      return;
    }

    const mode = pressedKey(document.getElementById("x-axis"), "distance");
    const xi = mode === "time" ? col("offset_s") : col("distance_m");
    const di = col("distance_m");
    if (xi < 0) return;
    // uPlot verlangt aufsteigende, nicht-leere x-Werte.
    let last = -Infinity;
    chartRows = rows.filter((r) => {
      const v = r[xi];
      if (v == null || !isFinite(v) || v < last) return false;
      last = v;
      return true;
    });
    const xs = chartRows.map((r) => (mode === "time" ? r[xi] : r[xi] / 1000));
    const get = (name) => { const i = col(name); return chartRows.map((r) => (i >= 0 ? r[i] : null)); };
    const hasData = (arr) => arr.some((v) => v != null);

    const c = {
      rider: cssVar("--rider", "#d9661a"), motor: cssVar("--motor", "#13877a"), alt: cssVar("--alt", "#6b6f9a"),
      ink: cssVar("--ink", "#1c2226"), ink2: cssVar("--ink-2", "#57605f"), ink3: cssVar("--ink-3", "#8a908c"),
      line: cssVar("--line", "#dcd8cf"),
    };
    const font = "11px " + (cssVar("--font", "sans-serif"));
    const axis = (extra) => Object.assign({
      stroke: c.ink2, font, grid: { stroke: c.line, width: 1 }, ticks: { stroke: c.line, width: 1 },
    }, extra || {});
    const xAxis = axis({
      values: mode === "time" ? (u, vals) => vals.map(hmm) : (u, vals) => vals.map((v) => fmt(v, v < 10 ? 1 : 0)),
    });
    const xLabel = mode === "time" ? tr("time") : tr("distance");
    const xValue = mode === "time" ? (u, v) => hmm(v) : (u, v) => (v == null ? "–" : fmt(v, 2) + " km");
    const val = (unit, digits) => (u, v) => (v == null ? "–" : fmt(v, digits) + " " + unit);
    const yVals = (u, vals) => vals.map((v) => fmt(v, 0));

    const defs = [];
    const alt = get("altitude_m");
    if (hasData(alt)) defs.push({ title: tr("c.alt"), data: [alt], series: [{ label: tr("c.alt"), stroke: c.alt, fill: withAlpha(c.alt, 0.25), value: val("m", 0) }] });
    const spd = get("speed_kmh");
    if (hasData(spd)) defs.push({ title: tr("c.speed"), data: [spd], series: [{ label: tr("c.speed"), stroke: c.ink2, value: val("km/h", 1) }] });
    const rp = get("rider_power_w"), mp = get("motor_power_w");
    if (hasData(rp) || hasData(mp)) {
      const d = { title: tr("c.power"), data: [], series: [] };
      if (hasData(rp)) { d.data.push(rp); d.series.push({ label: tr("s.rider"), stroke: c.rider, value: val("W", 0) }); }
      if (hasData(mp)) { d.data.push(mp); d.series.push({ label: tr("s.motor"), stroke: c.motor, value: val("W", 0) }); }
      defs.push(d);
    }
    const cad = get("cadence_rpm");
    if (hasData(cad)) defs.push({ title: tr("c.cadence"), data: [cad], series: [{ label: tr("c.cadence"), stroke: c.rider, value: val(tr("rpm"), 0) }] });
    const bat = get("battery_pct"), tmp = get("temperature_c");
    if (hasData(bat) || hasData(tmp)) {
      const d = { title: tr("c.battery"), data: [], series: [], axes: [] };
      if (hasData(bat)) {
        d.data.push(bat);
        d.series.push({ label: tr("c.battery"), stroke: c.motor, scale: "pct", value: val("%", 0) });
        d.axes.push(axis({ scale: "pct", values: (u, vs) => vs.map((v) => fmt(v, 0) + " %") }));
      }
      if (hasData(tmp)) {
        d.data.push(tmp);
        d.series.push({ label: tr("c.temp"), stroke: c.ink3, scale: "c", value: val("°C", 1) });
        d.axes.push(axis(Object.assign({ scale: "c", values: (u, vs) => vs.map((v) => fmt(v, 0) + " °C") },
          d.axes.length ? { side: 1, grid: { show: false } } : {})));
      }
      defs.push(d);
    }

    const width = Math.max(200, chartsEl.clientWidth || 600);
    defs.forEach((d) => {
      const box = document.createElement("div");
      box.className = "chart";
      box.setAttribute("aria-label", d.title);
      chartsEl.appendChild(box);
      const opts = {
        width, height: 170,
        cursor: { sync: { key: "ride-" + ride.id, setSeries: false }, drag: { x: false, y: false }, points: { size: 6 } },
        legend: { show: true },
        scales: { x: { time: false } },
        series: [{ label: xLabel, value: xValue }].concat(d.series.map((s) => Object.assign({ width: 1.5, spanGaps: false, points: { show: false } }, s))),
        axes: [xAxis].concat(d.axes || [axis({ values: yVals, size: 50 })]),
        hooks: {
          setCursor: [(u) => {
            const i = u.cursor.idx;
            if (i == null || di < 0) { if (u.cursor.left < 0) hideMarker(); return; }
            const r = chartRows[i];
            if (r) markerAtDistance(r[di]);
          }],
        },
      };
      if (d.axes && d.axes.length === 1) opts.axes[1].size = 60;
      try {
        charts.push(new uPlot(opts, [xs].concat(d.data), box));
      } catch (e) {
        box.remove();
      }
    });

    mapState.onHoverDistance = di < 0 ? null : (dist) => {
      const i = nearestIndex(chartRows.length, (k) => chartRows[k][di], dist);
      if (i < 0) return;
      charts.forEach((u) => {
        const left = u.valToPos(u.data[0][i], "x");
        if (isFinite(left)) u.setCursor({ left, top: 10 }, false);
      });
    };
  }

  function initCharts() {
    if (!chartsEl) return;
    buildCharts();
    const xAxisEl = document.getElementById("x-axis");
    if (xAxisEl) {
      xAxisEl.addEventListener("click", (ev) => {
        const b = ev.target.closest("button[data-key]");
        if (!b) return;
        setPressed(xAxisEl, b.dataset.key);
        buildCharts();
      });
    }
    if (typeof ResizeObserver !== "undefined") {
      let lastW = chartsEl.clientWidth;
      new ResizeObserver(() => {
        const w = chartsEl.clientWidth;
        if (!w || w === lastW) return;
        lastW = w;
        charts.forEach((u) => u.setSize({ width: Math.max(200, w), height: 170 }));
      }).observe(chartsEl);
    }
    // Farben neu lesen, wenn das Farbschema wechselt.
    if (window.matchMedia) {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const redo = () => {
        buildCharts();
        if (mapState.track) drawTrack(pressedKey(document.getElementById("color-by"), "speed"));
      };
      if (mq.addEventListener) mq.addEventListener("change", redo);
    }
  }

  /* ---------------------------------------------------------------- Start */

  try { initCharts(); } catch (e) { console.error("Diagramme:", e); }

  if (document.getElementById("map") && typeof L !== "undefined") {
    const url = "/api/v1/rides/" + encodeURIComponent(ride.id) + "/geojson";
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((geo) => { try { initMap(geo); } catch (e) { console.error("Karte:", e); } });
  }
})();
