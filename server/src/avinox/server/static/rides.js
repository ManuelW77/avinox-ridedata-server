// Fahrtenliste: Suche, Filter, Sortierung, Monatsgruppen. Der Server liefert alle Zeilen,
// dieses Skript blendet aus, sortiert um und rechnet die Summen neu. Sichtbar sind zunächst nur
// PAGE (30) Fahrten, der Knopf „Weitere anzeigen“ lädt mehr nach. Suche, Filter, Summen und
// Monatssummen laufen immer über ALLE passenden Fahrten; `limit` steht nicht in der URL.
(function () {
  "use strict";
  var tools = document.getElementById("rides-tools");
  var list = document.getElementById("rides-list");
  if (!tools || !list) return;

  var T = {};
  try { T = JSON.parse(document.getElementById("i18n").textContent) || {}; } catch (e) { /* Standardtexte */ }
  function tx(key, vars) {
    var s = T[key] || key;
    Object.keys(vars || {}).forEach(function (k) { s = s.split("{" + k + "}").join(vars[k]); });
    return s;
  }
  var lang = (document.documentElement.lang || "de").slice(0, 2) === "en" ? "en" : "de";
  var loc = lang === "en" ? "en-US" : "de-DE";
  function fmt(v, d) {
    return new Intl.NumberFormat(loc, { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
  }
  function dur(sec) {
    sec = Math.round(sec);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return h ? h + ":" + p(m) + ":" + p(s) : m + ":" + p(s);
  }
  function norm(s) { return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }

  var summary = document.getElementById("rides-summary");
  var summaryOrig = summary ? summary.textContent : "";
  var q = document.getElementById("rt-q");
  var yearBox = document.getElementById("rt-year");
  var lenBox = document.getElementById("rt-len");
  var gpsBtn = document.getElementById("rt-gps");
  var sortSel = document.getElementById("rt-sort");
  var resetBtns = [document.getElementById("rt-reset"), document.getElementById("rt-reset2")];
  var moreBtn = document.getElementById("rides-more");
  var PAGE = 30;
  var limit = PAGE;
  var emptyBox = document.getElementById("rides-empty");
  var bars = Array.prototype.slice.call(document.querySelectorAll("button.bar[data-ym]"));

  // Zeilen einlesen; Monats-/Wochentagsnamen und Datumsformen kommen zum Suchtext
  var monthFmt = new Intl.DateTimeFormat(loc, { month: "long", year: "numeric" });
  var wdFmt = new Intl.DateTimeFormat(loc, { weekday: "long" });
  var rows = Array.prototype.slice.call(list.children).map(function (li, i) {
    var d = li.dataset, start = d.start || "", extra = "";
    var date = start ? new Date(start + ":00") : null;
    if (date && !isNaN(date)) {
      var dd = ("0" + date.getDate()).slice(-2), mm = ("0" + (date.getMonth() + 1)).slice(-2);
      extra = [dd + "." + mm + "." + date.getFullYear(), date.getFullYear() + "-" + mm + "-" + dd,
        monthFmt.format(date), wdFmt.format(date)].join(" ");
    }
    return { li: li, i: i, start: start, ym: d.ym || "", km: +d.km || 0, hm: +d.hm || 0, dur: +d.dur || 0,
      mov: +d.mov || 0, gps: d.gps === "1", text: norm((d.search || "") + " " + extra) };
  });

  var years = {};
  rows.forEach(function (r) { if (r.ym) years[r.ym.slice(0, 4)] = true; });
  var yearList = Object.keys(years).sort().reverse();

  var state = { q: "", y: "", len: "", gps: false, sort: "new", m: "" };

  function mkBtn(label, value) {
    var b = document.createElement("button");
    b.type = "button"; b.dataset.v = value; b.textContent = label; b.setAttribute("aria-pressed", "false");
    return b;
  }
  if (yearList.length > 1) {
    yearBox.appendChild(mkBtn(tx("rides.all"), ""));
    yearList.forEach(function (y) { yearBox.appendChild(mkBtn(y, y)); });
    yearBox.hidden = false;
  }

  function readUrl() {
    var p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.y = yearList.indexOf(p.get("y")) >= 0 ? p.get("y") : "";
    state.len = ["lt20", "20-50", "gt50"].indexOf(p.get("len")) >= 0 ? p.get("len") : "";
    state.gps = p.get("gps") === "1";
    state.sort = ["new", "old", "dist", "hm", "dur"].indexOf(p.get("sort")) >= 0 ? p.get("sort") : "new";
    state.m = /^\d{4}-\d{2}$/.test(p.get("m") || "") ? p.get("m") : "";
  }
  function writeUrl() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.y) p.set("y", state.y);
    if (state.len) p.set("len", state.len);
    if (state.gps) p.set("gps", "1");
    if (state.sort !== "new") p.set("sort", state.sort);
    if (state.m) p.set("m", state.m);
    var qs = p.toString();
    try { history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash); } catch (e) { /* egal */ }
  }
  function active() { return !!(state.q || state.y || state.len || state.gps || state.m); }

  function pass(r) {
    if (state.q) {
      var words = norm(state.q).split(/\s+/).filter(Boolean);
      for (var i = 0; i < words.length; i++) if (r.text.indexOf(words[i]) < 0) return false;
    }
    if (state.y && r.ym.slice(0, 4) !== state.y) return false;
    if (state.m && r.ym !== state.m) return false;
    if (state.len === "lt20" && !(r.km < 20)) return false;
    if (state.len === "20-50" && !(r.km >= 20 && r.km <= 50)) return false;
    if (state.len === "gt50" && !(r.km > 50)) return false;
    if (state.gps && !r.gps) return false;
    return true;
  }

  var sorters = {
    new: function (a, b) { return (b.start || "").localeCompare(a.start || "") || a.i - b.i; },
    old: function (a, b) {
      if (!a.start !== !b.start) return a.start ? -1 : 1; // ohne Datum ans Ende
      return a.start.localeCompare(b.start) || a.i - b.i;
    },
    dist: function (a, b) { return b.km - a.km || a.i - b.i; },
    hm: function (a, b) { return b.hm - a.hm || a.i - b.i; },
    dur: function (a, b) { return b.dur - a.dur || a.i - b.i; }
  };

  function toggleSeg(box, value) {
    Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.v === value));
    });
  }

  function render() {
    var shown = rows.filter(pass);
    shown.sort(sorters[state.sort]);
    rows.forEach(function (r) { r.li.hidden = true; });
    Array.prototype.forEach.call(list.querySelectorAll("li.group-head"), function (h) { h.remove(); });

    var grouped = state.sort === "new" || state.sort === "old";
    var visible = shown.slice(0, limit);
    var perYm = {};
    shown.forEach(function (r) {
      var g = perYm[r.ym] || (perYm[r.ym] = { n: 0, km: 0 });
      g.n++; g.km += r.km;
    });
    var cur = null;
    visible.forEach(function (r) {
      if (grouped && (cur === null || r.ym !== cur)) {
        cur = r.ym;
        var head = document.createElement("li");
        head.className = "group-head";
        var date = r.ym ? new Date(r.ym + "-01T12:00:00") : null;
        head.textContent = tx("rides.group", {
          month: date ? monthFmt.format(date) : tx("rides.nodate"),
          n: perYm[r.ym].n, km: fmt(perYm[r.ym].km, 0)
        });
        list.appendChild(head);
      }
      r.li.hidden = false;
      list.appendChild(r.li);
    });
    var rest = shown.length - visible.length;
    if (moreBtn) {
      moreBtn.hidden = rest <= 0;
      moreBtn.textContent = tx("rides.more", { n: Math.min(PAGE, rest), rest: rest });
    }

    var km = 0, hm = 0, mov = 0;
    shown.forEach(function (r) { km += r.km; hm += r.hm; mov += r.mov; });
    if (summary) {
      summary.textContent = active()
        ? tx("rides.summary_filtered", { shown: shown.length, n: rows.length, km: fmt(km, 1), hm: fmt(hm, 0), h: dur(mov) })
        : summaryOrig;
    }
    emptyBox.hidden = shown.length > 0;

    toggleSeg(yearBox, state.y);
    toggleSeg(lenBox, state.len);
    gpsBtn.setAttribute("aria-pressed", String(state.gps));
    sortSel.value = state.sort;
    if (q.value !== state.q) q.value = state.q;
    resetBtns[0].hidden = !active();
    bars.forEach(function (b) {
      b.hidden = !!state.y && b.dataset.ym.slice(0, 4) !== state.y;
      b.setAttribute("aria-pressed", String(b.dataset.ym === state.m));
    });
    writeUrl();
  }

  var timer = null;
  q.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () { state.q = q.value.trim(); limit = PAGE; render(); }, 120);
  });
  yearBox.addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) return;
    state.y = b.dataset.v;
    if (state.m && state.y && state.m.slice(0, 4) !== state.y) state.m = "";
    limit = PAGE; render();
  });
  lenBox.addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b) return;
    state.len = b.dataset.v; limit = PAGE; render();
  });
  gpsBtn.addEventListener("click", function () { state.gps = !state.gps; limit = PAGE; render(); });
  sortSel.addEventListener("change", function () { state.sort = sortSel.value; limit = PAGE; render(); });
  bars.forEach(function (b) {
    b.addEventListener("click", function () {
      state.m = state.m === b.dataset.ym ? "" : b.dataset.ym;
      limit = PAGE; render();
    });
  });
  resetBtns.forEach(function (b) {
    if (b) b.addEventListener("click", function () {
      state.q = ""; state.y = ""; state.len = ""; state.gps = false; state.m = "";
      limit = PAGE; render();
    });
  });
  if (moreBtn) moreBtn.addEventListener("click", function () {
    var y = window.scrollY;
    limit += PAGE; render();
    window.scrollTo(0, y);
  });

  readUrl();
  tools.classList.add("ready");
  render();
})();
