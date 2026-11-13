/* Fahrtansicht: Karte (MapLibre, mit 3D-Ansicht) und Verlaufsdiagramme (uPlot), gekoppelt über die Strecke. */
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

  const mapState = { map: null, ready: false, track: null, onHoverDistance: null };

  const SCALE = ["#2c7bb6", "#7fbfd4", "#e8d86a", "#f08a3e", "#c62e1d"];
  const MODE_COLORS = ["#8a908c", "#3a9a5b", "#2c7bb6", "#c62e1d", "#8e4fb3", "#e0a21a", "#13877a", "#d9661a", "#5b6abf", "#b0417a"];

  const COLOR_KEYS = {
    speed: { field: "speed_kmh", unit: "km/h", digits: 1 },
    rider: { field: "rider_power_w", unit: "W", digits: 0 },
    motor: { field: "motor_power_w", unit: "W", digits: 0 },
    alt: { field: "altitude_m", unit: "m", digits: 0 },
    mode: { field: "assist_level" },
  };

  const EMPTY_FC = { type: "FeatureCollection", features: [] };
  const currentKey = () => pressedKey(document.getElementById("color-by"), "speed");

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

  function legendInto(el, items) {
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

  const legend = (items) => legendInto(document.getElementById("map-legend"), items);

  // Spur als Segmente gleicher Farbe: { features, items } (items = Legende)
  function colorize(key) {
    const t = mapState.track;
    const cfg = COLOR_KEYS[key] || COLOR_KEYS.speed;
    const fi = t.fields.indexOf(cfg.field);
    const vals = t.samples.map((s) => (s && fi >= 0 ? s[fi] : null));
    const grey = cssVar("--ink-3", "#8a908c");
    const items = [];
    let classes, palette;

    if (key === "mode") {
      const seen = new Map();
      const colorFor = (v) => {
        if (v == null) return -1;
        const k = Math.round(v);
        if (!seen.has(k)) seen.set(k, MODE_COLORS[((k % MODE_COLORS.length) + MODE_COLORS.length) % MODE_COLORS.length]);
        return k;
      };
      classes = vals.map(colorFor);
      [...seen.keys()].sort((a, b) => a - b).forEach((k) => items.push([seen.get(k), modeName(k)]));
      palette = (c) => (c === -1 ? grey : seen.get(c));
    } else {
      const br = quantileBreaks(vals);
      if (br) {
        const edges = [br.min, ...br.cuts, br.max];
        for (let i = 0; i < 5; i++) {
          if (i > 0 && edges[i] === edges[i + 1] && edges[i] === edges[i - 1]) continue;
          items.push([SCALE[i], fmt(edges[i], cfg.digits) + "–" + fmt(edges[i + 1], cfg.digits) + " " + cfg.unit]);
        }
        classes = vals.map((v) => classOf(v, br.cuts));
        palette = (c) => (c === -1 ? grey : SCALE[c]);
      } else {
        classes = vals.map(() => -1);
        palette = () => grey;
        items.push([grey, tr("no_values")]);
      }
    }
    return { features: buildSegments(t, classes, palette), items };
  }

  function buildSegments(t, classes, palette) {
    const feats = [];
    const n = t.coords.length;
    let start = 0;
    for (let i = 1; i <= n; i++) {
      if (i === n || classes[i] !== classes[start]) {
        const pts = t.coords.slice(start, Math.min(i + 1, n));
        if (pts.length > 1) {
          feats.push({ type: "Feature", properties: { color: palette(classes[start]) }, geometry: { type: "LineString", coordinates: pts } });
        }
        start = i;
      }
    }
    return feats;
  }

  // Farbige Spur samt weißer Kontur in eine Karte einbauen (2D und 3D)
  function addTrackLayers(map, features, width) {
    map.addSource("track", { type: "geojson", data: { type: "FeatureCollection", features } });
    map.addLayer({ id: "track-casing", type: "line", source: "track", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#ffffff", "line-width": width + 3, "line-opacity": 0.9 } });
    map.addLayer({ id: "track", type: "line", source: "track", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": width } });
  }

  function drawTrack(key) {
    const t = mapState.track;
    if (!t || !mapState.ready) return;
    const r = colorize(key);
    const src = mapState.map.getSource("track");
    if (src) src.setData({ type: "FeatureCollection", features: r.features });
    legend(r.items);
  }

  function dot(color, title) {
    const d = document.createElement("div");
    d.style.cssText = "width:12px;height:12px;border-radius:50%;box-sizing:border-box;border:2px solid " + cssVar("--surface", "#fffdf9")
      + ";background:" + color;
    d.title = title;
    return d;
  }

  function initMap(geo) {
    const el = document.getElementById("map");
    if (!el || typeof maplibregl === "undefined" || !ride.map_style) return;
    const feats = (geo && Array.isArray(geo.features)) ? geo.features : [];
    const findKind = (kind, type) => feats.find((f) => f && f.properties && f.properties.kind === kind
      && f.geometry && f.geometry.type === type);
    const trackF = findKind("track", "LineString");
    const ink = cssVar("--ink", "#1c2226");

    let trackLine = null, previewPts = [];
    if (trackF && trackF.geometry.coordinates.length > 1) {
      const p = trackF.properties;
      const coords = trackF.geometry.coordinates.map((c) => [c[0], c[1]]);
      const samples = Array.isArray(p.samples) ? p.samples : [];
      const tf = Array.isArray(p.fields) ? p.fields : [];
      trackLine = coords;
      mapState.track = { coords, samples: coords.map((_, i) => samples[i] || null), fields: tf, di: tf.indexOf("distance_m") };
    } else {
      const prevF = findKind("preview", "LineString");
      previewPts = prevF ? prevF.geometry.coordinates.map((c) => [c[0], c[1]]) : [];
      if (previewPts.length < 2 && Array.isArray(ride.preview)) {
        previewPts = ride.preview.filter((q) => Array.isArray(q) && q.length >= 2).map((q) => [q[1], q[0]]);
      }
    }

    // Ausschnitt: bbox, sonst Spur bzw. Vorschau
    const bounds = new maplibregl.LngLatBounds();
    let hasBounds = false;
    if (geo && Array.isArray(geo.bbox) && geo.bbox.length >= 4) {
      const b = geo.bbox, n = b.length >= 6 ? 3 : 2;
      bounds.extend([b[0], b[1]]); bounds.extend([b[n], b[n + 1]]);
      hasBounds = true;
    } else {
      (trackLine || previewPts).forEach((c) => { bounds.extend(c); hasBounds = true; });
    }
    const map = new maplibregl.Map(Object.assign({
      container: el, style: ride.map_style, attributionControl: { compact: true },
    }, hasBounds ? { bounds, fitBoundsOptions: { padding: 20 } } : { center: [0, 0], zoom: 1 }));
    mapState.map = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("error", (ev) => { if (ev && ev.error) console.warn("Karte:", ev.error.message || ev.error); });

    map.on("load", () => {
      if (mapState.track) {
        const t = mapState.track;
        const r = colorize(currentKey());
        addTrackLayers(map, r.features, 4.5);
        legend(r.items);
        map.addSource("hover", { type: "geojson", data: EMPTY_FC });
        map.addLayer({ id: "hover", type: "circle", source: "hover",
          paint: { "circle-radius": 7, "circle-color": ink, "circle-stroke-color": cssVar("--surface", "#fffdf9"), "circle-stroke-width": 3 } });
        mapState.ready = true;

        // Hover auf der Karte -> Diagramm-Cursor (nächster Spurpunkt, höchstens 20 px entfernt)
        map.on("mousemove", (ev) => {
          if (t.di < 0 || !mapState.onHoverDistance) return;
          let best = -1, bestD = Infinity;
          for (let i = 0; i < t.coords.length; i++) {
            const q = map.project(t.coords[i]);
            const d = (q.x - ev.point.x) ** 2 + (q.y - ev.point.y) ** 2;
            if (d < bestD) { bestD = d; best = i; }
          }
          if (best >= 0 && bestD < 400) {
            const s = t.samples[best];
            if (s && s[t.di] != null) {
              showMarker(t.coords[best]);
              mapState.onHoverDistance(s[t.di]);
            }
          }
        });
        setup3d();
      } else if (previewPts.length > 1) {
        const c = cssVar("--alt", "#6b6f9a");
        map.addSource("preview", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: previewPts } } });
        map.addLayer({ id: "preview", type: "line", source: "preview", paint: { "line-color": c, "line-width": 3, "line-dasharray": [2, 2] } });
        legend([[c, tr("preview_nogps")]]);
      }

      ["start", "end"].forEach((kind) => {
        const f = findKind(kind, "Point");
        if (!f) return;
        const label = kind === "start" ? tr("start") : tr("end");
        new maplibregl.Marker({ element: dot(kind === "start" ? cssVar("--motor", "#13877a") : ink, label) })
          .setLngLat(f.geometry.coordinates.slice(0, 2)).addTo(map);
      });
    });
  }

  function showMarker(lngLat) {
    const src = mapState.ready && mapState.map.getSource("hover");
    if (src) src.setData({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: lngLat } });
  }

  function markerAtDistance(dist) {
    const t = mapState.track;
    if (!t || t.di < 0 || dist == null) return;
    const i = nearestIndex(t.samples.length, (k) => (t.samples[k] ? t.samples[k][t.di] : null), dist);
    if (i >= 0) showMarker(t.coords[i]);
  }

  function hideMarker() {
    const src = mapState.ready && mapState.map.getSource("hover");
    if (src) src.setData(EMPTY_FC);
  }

  /* --------------------------------------------------------------- 3D-Ansicht */

  const DEM_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
  // Höhenüberhöhung (Standard leicht überhöht, sonst wirken Mittelgebirge platt); Wahl bleibt im Browser erhalten
  const EXAG_CHOICES = [1, 1.5, 2, 3];
  let exaggeration = 1.5;
  try {
    const saved = Number(localStorage.getItem("avinox3dExag"));
    if (EXAG_CHOICES.includes(saved)) exaggeration = saved;
  } catch (e) { /* Speicher gesperrt: Standard bleibt */ }
  const LOOK_AHEAD_M = 70;   // Richtung: Verlauf über die nächsten Meter
  const CHASE_BACK_M = 140, CHASE_UP_M = 70, CHASE_TARGET_M = 40;  // Kamera hinter und über dem Fahrer, Blick voraus

  let map3d = null, play3d = null, opener3d = null;
  const byId = (id) => document.getElementById(id);

  const toLngLat = (p) => new maplibregl.LngLat(p[1], p[0]);  // p = [lat, lon]
  function distM(a, b) {
    const r = Math.PI / 180, R = 6371000;
    const dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function headingDeg(a, b) {
    const r = Math.PI / 180;
    const y = Math.sin((b[1] - a[1]) * r) * Math.cos(b[0] * r);
    const x = Math.cos(a[0] * r) * Math.sin(b[0] * r) - Math.sin(a[0] * r) * Math.cos(b[0] * r) * Math.cos((b[1] - a[1]) * r);
    return (Math.atan2(y, x) / r + 360) % 360;
  }
  // Punkt `meters` m von p in Richtung `bearing` (Grad)
  function offsetPt(p, bearing, meters) {
    const r = Math.PI / 180, a = bearing * r;
    return [p[0] + meters * Math.cos(a) / 111320, p[1] + meters * Math.sin(a) / (111320 * Math.cos(p[0] * r))];
  }
  // Geländehöhe in m (überhöht, wie gezeichnet); ohne geladene Kachel 0
  function ground(map, p) {
    try {
      const h = map.terrain ? map.terrain.getElevationForLngLatZoom(toLngLat(p), 14) : 0;
      return isFinite(h) ? h : 0;
    } catch (e) { return 0; }
  }

  function setup3d() {
    const btn = byId("open3d");
    if (!btn || !byId("view3d")) return;
    btn.hidden = false;
    btn.addEventListener("click", open3d);
    byId("close3d").addEventListener("click", close3d);
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !byId("view3d").hidden) close3d(); });
    // Überhöhung: Listener nur hier, einmalig (setup3d läuft einmal pro Seite)
    const exagEl = byId("exag");
    if (exagEl) {
      setPressed(exagEl, String(exaggeration));
      exagEl.addEventListener("click", (ev) => {
        const b = ev.target.closest("button[data-key]");
        const v = b ? Number(b.dataset.key) : NaN;
        if (!EXAG_CHOICES.includes(v)) return;
        exaggeration = v;
        try { localStorage.setItem("avinox3dExag", String(v)); } catch (e) { /* egal */ }
        setPressed(exagEl, String(v));
        if (map3d && map3d.getSource("dem")) map3d.setTerrain({ source: "dem", exaggeration });
        if (play3d) play3d.refresh();
      });
    }
  }

  function open3d() {
    opener3d = document.activeElement;
    const title = document.getElementById("ride-title-text");
    byId("view3d-title").textContent = title ? title.textContent : "";
    byId("view3d").hidden = false;
    document.body.style.overflow = "hidden";
    legendInto(byId("legend3d"), []);
    show3d();
    byId("close3d").focus();
  }

  function close3d() {
    if (play3d) { play3d.stop(); play3d = null; }
    byId("view3d").hidden = true;
    document.body.style.overflow = "";
    if (map3d) { map3d.remove(); map3d = null; }
    if (opener3d && opener3d.focus) opener3d.focus();
  }

  // Blickrichtung: Hauptrichtung Start -> Ziel
  function bearing3d(coords) {
    const a = coords[0], b = coords[coords.length - 1];
    return headingDeg([a[1], a[0]], [b[1], b[0]]);
  }

  function show3d() {
    const t = mapState.track;
    if (!t || typeof maplibregl === "undefined") { byId("legend3d").textContent = tr("d3.failed"); return; }
    const coords = t.coords;
    const bounds = coords.reduce((box, c) => box.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));
    const demAttr = `<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md" target="_blank" rel="noopener">${tr("d3.elev")}: Mapzen Terrain Tiles</a>`;
    const map = new maplibregl.Map({
      container: "map3d", style: ride.map_style, bounds, fitBoundsOptions: { padding: 80 },
      maxPitch: 80, attributionControl: { compact: true },
    });
    map3d = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.on("error", (ev) => { if (ev && ev.error) console.warn("3D:", ev.error.message || ev.error); });
    map.on("load", () => {
      if (map !== map3d) return;
      map.addSource("dem", { type: "raster-dem", tiles: [DEM_TILES], encoding: "terrarium", tileSize: 256, maxzoom: 15, attribution: demAttr });
      // eigene Quelle fürs Relief, MapLibre empfiehlt sie getrennt vom Gelände
      map.addSource("dem-shade", { type: "raster-dem", tiles: [DEM_TILES], encoding: "terrarium", tileSize: 256, maxzoom: 15 });
      const firstSymbol = (map.getStyle().layers.find((l) => l.type === "symbol") || {}).id;
      map.addLayer({ id: "hillshade", type: "hillshade", source: "dem-shade",
        paint: { "hillshade-exaggeration": 0.35, "hillshade-shadow-color": "#3a3a3a" } }, firstSymbol);
      map.setTerrain({ source: "dem", exaggeration });
      if (map.setSky) map.setSky({ "sky-color": "#a9cbe8", "horizon-color": "#e9eef2", "sky-horizon-blend": 0.6 });

      const r = colorize(currentKey());
      addTrackLayers(map, r.features, 5);
      legendInto(byId("legend3d"), r.items);

      const first = coords[0], last = coords[coords.length - 1];
      new maplibregl.Marker({ element: dot(cssVar("--motor", "#13877a"), tr("start")) }).setLngLat(first).addTo(map);
      new maplibregl.Marker({ element: dot(cssVar("--ink", "#1c2226"), tr("end")) }).setLngLat(last).addTo(map);

      // Einpassen erst nach den Höhenkacheln (sonst rechnet die Kamera mit Meereshöhe), dann kippen
      let fitted = false;
      const fit = () => {
        if (fitted || map !== map3d) return;
        fitted = true;
        map.off("sourcedata", onData);
        const side = Math.min(80, Math.round(map.getContainer().clientWidth * 0.08));
        const camera = map.cameraForBounds(bounds, { padding: { top: 90, bottom: 130, left: side, right: side }, bearing: bearing3d(coords) });
        if (!camera) return;
        map.jumpTo(Object.assign({}, camera, { pitch: 0 }));
        map.easeTo({ pitch: 62, zoom: camera.zoom - 0.2, duration: 1400 });
      };
      const onData = (ev) => { if (ev.sourceId === "dem" && map.isSourceLoaded("dem")) setTimeout(fit, 150); };
      map.on("sourcedata", onData);
      setTimeout(fit, 5000);
      play3d = setupPlayback(map, coords);
    });
  }

  /* ----------------------------------------------------------------- Abspielen */

  function setupPlayback(map, coords) {
    const t = mapState.track;
    const btn = byId("play-btn"), dock = byId("play-dock"), hud = byId("play-hud"), seek = byId("play-seek"), speedEl = byId("play-speed");
    const points = coords.map((c) => [c[1], c[0]]);   // [lat, lon], immer in Aufzeichnungsrichtung
    const lengths = [0];
    for (let i = 1; i < points.length; i++) lengths.push(lengths[i - 1] + distM(points[i - 1], points[i]));
    const total = lengths[lengths.length - 1];
    if (total < 20) return null;

    // Stelle bei `meters`: Segmentindex i (>=1) und Anteil f im Segment
    const locate = (meters) => {
      const target = Math.min(Math.max(meters, 0), total);
      let lo = 1, hi = points.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (lengths[mid] < target) lo = mid + 1; else hi = mid; }
      const piece = lengths[lo] - lengths[lo - 1] || 1;
      return { i: lo, f: Math.min(Math.max((target - lengths[lo - 1]) / piece, 0), 1) };
    };
    const at = (meters) => {
      const { i, f } = locate(meters);
      return [points[i - 1][0] + (points[i][0] - points[i - 1][0]) * f, points[i - 1][1] + (points[i][1] - points[i - 1][1]) * f];
    };
    const altIdx = t.fields.indexOf("altitude_m");
    const altAt = (meters) => {   // Notnagel ohne Gelände: Höhe aus den Messwerten
      const { i } = locate(meters);
      const s = t.samples[i];
      return s && altIdx >= 0 && s[altIdx] != null ? s[altIdx] * exaggeration : 0;
    };
    const heightAt = (meters) => { const g = ground(map, at(meters)); return g > 0 ? g : altAt(meters); };

    // aufgezeichnete Strecke an der Stelle (für Anzeige und Wertesuche)
    const recAt = (meters) => {
      if (t.di < 0) return meters;
      const { i, f } = locate(meters);
      const a = t.samples[i - 1], b = t.samples[i];
      if (!a || !b || a[t.di] == null || b[t.di] == null) return meters;
      return a[t.di] + (b[t.di] - a[t.di]) * f;
    };
    const recTotal = (() => {
      for (let i = t.samples.length - 1; i >= 0; i--) { const s = t.samples[i]; if (s && t.di >= 0 && s[t.di] != null) return s[t.di]; }
      return total;
    })();

    // Werte aus der Zeitreihe am nächsten Punkt
    const dcol = col("distance_m");
    const colIdx = { time: col("offset_s"), speed: col("speed_kmh"), rider: col("rider_power_w"), motor: col("motor_power_w"), grade: col("gradient_pct") };
    const cells = [];
    const addCell = (key, label) => {
      const span = document.createElement("span");
      const small = document.createElement("small");
      small.textContent = label;
      const b = document.createElement("b");
      span.append(small, b);
      hud.appendChild(span);
      cells.push({ key, b });
    };
    hud.textContent = "";
    addCell("dist", tr("d3.dist"));
    if (colIdx.time >= 0) addCell("time", tr("d3.time"));
    if (colIdx.speed >= 0) addCell("speed", tr("c.speed"));
    if (colIdx.rider >= 0) addCell("rider", tr("s.rider"));
    if (colIdx.motor >= 0) addCell("motor", tr("s.motor"));
    if (colIdx.grade >= 0) addCell("grade", tr("d3.grade"));
    const hms = (sec) => { const s = Math.round(sec), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
      return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s % 60).padStart(2, "0"); };
    const updateHud = (meters) => {
      const d = recAt(meters);
      const ri = dcol >= 0 ? nearestIndex(rows.length, (k) => rows[k][dcol], d) : -1;
      const row = ri >= 0 ? rows[ri] : null;
      const v = (key) => (row ? row[colIdx[key]] : null);
      cells.forEach(({ key, b }) => {
        if (key === "dist") b.textContent = fmt(d / 1000, 1) + " / " + fmt(recTotal / 1000, 1) + " km";
        else if (key === "time") b.textContent = v("time") == null ? "–" : hms(v("time"));
        else if (key === "speed") b.textContent = v("speed") == null ? "–" : fmt(v("speed"), 0) + " km/h";
        else if (key === "rider") b.textContent = v("rider") == null ? "–" : fmt(v("rider"), 0) + " W";
        else if (key === "motor") b.textContent = v("motor") == null ? "–" : fmt(v("motor"), 0) + " W";
        else if (key === "grade") b.textContent = v("grade") == null ? "–" : (v("grade") >= 0 ? "↗ " : "↘ ") + fmt(Math.abs(v("grade")), 0) + " %";
      });
    };

    map.addSource("rider", { type: "geojson", data: { type: "Point", coordinates: coords[0] } });
    map.addLayer({ id: "rider", type: "circle", source: "rider",
      paint: { "circle-radius": 8, "circle-color": cssVar("--rider", "#d9661a"), "circle-stroke-color": "#ffffff", "circle-stroke-width": 3 } });

    const baseMs = Math.min(150, Math.max(25, total / 15)) * 1000;
    let speed = 1, progress = 0, playing = false, frame = null, lastTs = null;
    let smoothed = null, smBack = null, smTarget = null;
    const lp = (prev, v) => (prev == null ? v : prev + (v - prev) * 0.2);

    const label = () => {
      btn.textContent = playing ? tr("d3.pause") : progress > 0 && progress < 1 ? tr("d3.resume") : tr("d3.play");
      btn.setAttribute("aria-pressed", String(playing));
    };
    const render = () => {
      const meters = progress * total, here = at(meters);
      const course = headingDeg(at(meters - LOOK_AHEAD_M / 3), at(meters + LOOK_AHEAD_M));
      // Richtung weich nachführen (kürzester Weg um 0°/360°), sonst ruckelt sie an jedem Knick
      smoothed = smoothed === null ? course : (smoothed + ((course - smoothed + 540) % 360 - 180) * 0.06 + 360) % 360;
      map.getSource("rider").setData({ type: "Point", coordinates: [here[1], here[0]] });
      const behind = offsetPt(here, smoothed + 180, CHASE_BACK_M), ahead = offsetPt(here, smoothed, CHASE_TARGET_M);
      smBack = lp(smBack, Math.max(heightAt(meters - CHASE_BACK_M), heightAt(meters)) + CHASE_UP_M * exaggeration);
      smTarget = lp(smTarget, heightAt(meters + CHASE_TARGET_M));
      const camera = map.calculateCameraOptionsFromTo(toLngLat(behind), smBack, toLngLat(ahead), smTarget);
      // MapLibre führt die Höhe des Mittelpunkts nur verzögert nach, sonst läge die Kamera im Berg
      map.transform.elevation = smTarget;
      map.jumpTo(camera);
      updateHud(meters);
      seek.value = String(Math.round(progress * 1000));
    };
    const step = (now) => {
      if (!playing) return;
      if (lastTs !== null) progress = Math.min(1, progress + (now - lastTs) / (baseMs / speed));
      lastTs = now;
      render();
      if (progress >= 1) { pause(); return; }
      frame = requestAnimationFrame(step);
    };
    const play = () => {
      if (progress >= 1) { progress = 0; smoothed = smBack = smTarget = null; }
      playing = true; lastTs = null; label();
      frame = requestAnimationFrame(step);
    };
    function pause() {
      playing = false;
      if (frame) cancelAnimationFrame(frame);
      frame = null; label();
    }
    const toggle = () => (playing ? pause() : play());
    const onSeek = () => {
      progress = Number(seek.value) / 1000;
      smoothed = smBack = smTarget = null;
      lastTs = null;
      render();
      label();
    };
    const onSpeed = (ev) => {
      const b = ev.target.closest("button[data-key]");
      if (!b) return;
      speed = Number(b.dataset.key) || 1;
      setPressed(speedEl, b.dataset.key);
    };
    const stopByUser = (ev) => { if (playing && ev.originalEvent) pause(); };
    ["dragstart", "zoomstart", "rotatestart", "pitchstart"].forEach((n) => map.on(n, stopByUser));
    btn.addEventListener("click", toggle);
    seek.addEventListener("input", onSeek);
    speedEl.addEventListener("click", onSpeed);
    seek.value = "0";
    updateHud(0);
    btn.hidden = false; dock.hidden = false; speedEl.hidden = false;
    label();
    return {
      // nach Wechsel der Überhöhung: Glättung zurücksetzen, bei Pause Kamera einmal neu setzen
      refresh() {
        smBack = smTarget = null;
        if (!playing && progress > 0) render();
      },
      stop() {
        pause();
        btn.removeEventListener("click", toggle);
        seek.removeEventListener("input", onSeek);
        speedEl.removeEventListener("click", onSpeed);
        btn.hidden = true; dock.hidden = true; speedEl.hidden = true;
      },
    };
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
        if (mapState.track) drawTrack(currentKey());
      };
      if (mq.addEventListener) mq.addEventListener("change", redo);
      document.addEventListener("themechange", redo);
    }
  }

  /* ---------------------------------------------------------------- Start */

  try { initCharts(); } catch (e) { console.error("Diagramme:", e); }

  const colorByEl = document.getElementById("color-by");
  if (colorByEl) {
    colorByEl.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-key]");
      if (!b) return;
      setPressed(colorByEl, b.dataset.key);
      drawTrack(b.dataset.key);
    });
  }

  if (document.getElementById("map") && typeof maplibregl !== "undefined") {
    const url = "/api/v1/rides/" + encodeURIComponent(ride.id) + "/geojson";
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((geo) => { try { initMap(geo); } catch (e) { console.error("Karte:", e); } });
  }
})();
