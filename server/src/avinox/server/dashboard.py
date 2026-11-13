"""Übersicht über alle Fahrten: Zeitreihen je Tag/Woche/Monat/Jahr, Vergleich und Bestwerte.

Gelesen wird nur aus der Tabelle ``rides`` (plus wenigen Feldern aus
``analysis_json`` per ``json_extract``) – die Rohdateien bleiben unangetastet.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

from ..analysis import ASSIST_LEVELS, GRADIENT_BINS, _label
from . import i18n
from .i18n import t

PERIODS: dict[str, int] = {"day": 30, "week": 26, "month": 24, "year": 5, "all": 100}
# Mindestanzahl Balken in den Diagrammen, damit eine einzelne Fahrt nicht die ganze Breite füllt
MIN_BUCKETS: dict[str, int] = {"day": 6, "week": 6, "month": 6, "year": 2, "all": 2}
"""Zeitraum -> Anzahl der Balken in der Zeitreihe."""

MIN_KM_BEST = 1.0
"""Kürzere Fahrten (Testfahrten) zählen nicht für Bestwerte."""
MIN_KM_SELF = 2.0
"""„Am meisten selbst getreten“ erst ab dieser Strecke – sonst gewinnt jede Rollerei."""
MIN_BATTERY_PCT = 5
"""„Sparsamste Fahrt“ nur bei mindestens so viel Akkuverbrauch (sonst rundungsgetrieben)."""

WEEKDAYS = i18n.WEEKDAYS["de"]
MONTHS = i18n.MONTHS["de"]
MONTHS_SHORT = i18n.MONTHS_SHORT["de"]

RIDE_SQL = """
SELECT id, ride_id, start_utc, distance_m, duration_s, moving_time_s, ascent_m, max_speed_kmh,
       avg_rider_power_w, calories_kcal, battery_start_pct, battery_end_pct,
       json_extract(analysis_json, '$.energy.rider_kj') AS rider_kj,
       json_extract(analysis_json, '$.energy.motor_kj') AS motor_kj,
       json_extract(analysis_json, '$.energy.motor_wh') AS motor_wh,
       json_extract(analysis_json, '$.energy.motor_share_pct') AS motor_share_pct,
       json_extract(analysis_json, '$.battery.used_pct') AS battery_used_pct,
       json_extract(analysis_json, '$.battery.km_per_pct') AS km_per_pct,
       json_extract(analysis_json, '$.summary.max_rider_power_w') AS max_rider_power_w,
       json_extract(analysis_json, '$.summary.max_motor_power_w') AS max_motor_power_w,
       json_extract(analysis_json, '$.best_efforts.rider_power_w."300s"') AS best_5min_w,
       json_extract(analysis_json, '$.best_efforts.rider_power_w."1200s"') AS best_20min_w,
       json_extract(analysis_json, '$.assist') AS assist_json,
       json_extract(analysis_json, '$.gears') AS gears_json,
       json_extract(analysis_json, '$.by_gradient') AS by_gradient_json,
       json_extract(analysis_json, '$.distributions') AS dist_json
FROM rides
ORDER BY start_utc IS NULL, start_utc DESC, path DESC
"""


# ----------------------------------------------------------------- Formatierung


def num(value: float | None, digits: int = 0, lang: str = "de") -> str:
    """Zahl je Sprache: 1.234,5 bzw. 1,234.5"""
    return i18n.num(value, digits, lang)


def emphasize(sentence: str, value_text: str) -> Markup:
    """Satz als HTML, der Wert darin fett."""
    safe = escape(sentence)
    return Markup(safe.replace(escape(value_text), Markup("<strong>") + escape(value_text) + Markup("</strong>"), 1))


def day_text(d: date, lang: str = "de") -> str:
    return i18n.day_text(d, lang)


# ----------------------------------------------------------------- Zeiträume


def period_start(d: date, period: str) -> date:
    if period == "day":
        return d
    if period == "week":
        return d - timedelta(days=d.weekday())
    if period == "month":
        return d.replace(day=1)
    return d.replace(month=1, day=1)


def shift(start: date, period: str, n: int) -> date:
    """Beginn des Zeitraums ``n`` Schritte weiter (negativ: zurück)."""
    if period == "day":
        return start + timedelta(days=n)
    if period == "week":
        return start + timedelta(weeks=n)
    if period == "month":
        m = start.year * 12 + start.month - 1 + n
        return date(m // 12, m % 12 + 1, 1)
    return date(start.year + n, 1, 1)


def steps_between(a: date, b: date, period: str) -> int:
    """Wie viele Zeiträume liegt ``b`` hinter ``a`` (beides Zeitraum-Anfänge)."""
    if period == "day":
        return (a - b).days
    if period == "week":
        return (a - b).days // 7
    if period == "month":
        return (a.year * 12 + a.month) - (b.year * 12 + b.month)
    return a.year - b.year


def period_key(start: date, period: str) -> str:
    if period == "day":
        return start.isoformat()
    if period == "week":
        y, w, _ = start.isocalendar()
        return f"{y}-W{w:02d}"
    if period == "month":
        return f"{start:%Y-%m}"
    return str(start.year)


def period_label(start: date, period: str, lang: str = "de") -> str:
    """Kurz für die Diagrammachse."""
    if period == "day":
        return f"{start.day}.{start.month}."
    if period == "week":
        return t("week.short", lang, w=start.isocalendar()[1])
    if period == "month":
        return f"{i18n.MONTHS_SHORT[i18n.norm(lang)][start.month - 1]} {start:%y}"
    return str(start.year)


def period_title(start: date, period: str, lang: str = "de") -> str:
    """Ausführlich, z. B. für Überschriften und Sätze."""
    if period == "day":
        return day_text(start, lang)
    if period == "week":
        y, w, _ = start.isocalendar()
        end = start + timedelta(days=6)
        first = f"{start:%d.%m.}" if i18n.norm(lang) == "de" else f"{start:%m-%d}"
        return t("week.title", lang, w=w, y=y, a=first, b=i18n.date_text(end, lang))
    if period == "month":
        return f"{i18n.MONTHS[i18n.norm(lang)][start.month - 1]} {start.year}"
    return str(start.year)


def period_end(start: date, period: str) -> date:
    """Letzter Tag des Zeitraums."""
    return shift(start, period, 1) - timedelta(days=1)


# ----------------------------------------------------------------- Summen


def _empty() -> dict:
    return {"rides": 0, "distance_km": 0.0, "moving_time_s": 0, "ascent_m": 0.0, "rider_kj": 0.0, "motor_kj": 0.0,
            "motor_wh": 0.0, "calories_kcal": 0.0, "battery_used_pct": 0, "_power_time": 0.0, "_power_s": 0}


def _add(acc: dict, r: dict) -> None:
    acc["rides"] += 1
    acc["distance_km"] += (r["distance_m"] or 0) / 1000
    acc["moving_time_s"] += r["moving_time_s"] or 0
    acc["ascent_m"] += r["ascent_m"] or 0
    acc["rider_kj"] += r["rider_kj"] or 0
    acc["motor_kj"] += r["motor_kj"] or 0
    acc["motor_wh"] += r["motor_wh"] or 0
    acc["calories_kcal"] += r["calories_kcal"] or 0
    acc["battery_used_pct"] += max(0, r["battery_used_pct"] or 0)
    if r["avg_rider_power_w"] is not None and r["moving_time_s"]:
        acc["_power_time"] += r["avg_rider_power_w"] * r["moving_time_s"]
        acc["_power_s"] += r["moving_time_s"]


def _finish(acc: dict) -> dict:
    energy = acc["rider_kj"] + acc["motor_kj"]
    return {
        "rides": acc["rides"],
        "distance_km": round(acc["distance_km"], 2),
        "moving_time_s": acc["moving_time_s"],
        "ascent_m": round(acc["ascent_m"]),
        "rider_kj": round(acc["rider_kj"], 1),
        "motor_kj": round(acc["motor_kj"], 1),
        "motor_wh": round(acc["motor_wh"], 1),
        "motor_share_pct": round(100 * acc["motor_kj"] / energy, 1) if energy else None,
        "calories_kcal": round(acc["calories_kcal"]),
        "battery_used_pct": acc["battery_used_pct"],
        "avg_rider_power_w": round(acc["_power_time"] / acc["_power_s"]) if acc["_power_s"] else None,
    }


COMPARE_KEYS = ("rides", "distance_km", "moving_time_s", "ascent_m", "rider_kj", "motor_wh", "motor_share_pct",
                "calories_kcal", "battery_used_pct", "avg_rider_power_w")


def compare(cur: dict, prev: dict) -> dict:
    out = {}
    for k in COMPARE_KEYS:
        a, b = cur.get(k), prev.get(k)
        delta = None if a is None or b is None else round(a - b, 2)
        pct = round(100 * (a - b) / b) if a is not None and b else None
        out[k] = {"current": a, "previous": b, "delta": delta, "delta_pct": pct}
    return out


# ----------------------------------------------------------------- Bestwerte


def _when(r: dict, lang: str = "de") -> str:
    """Satzanfang: „Am Do 05.11.2026“ bzw. „Bei Fahrt 7 (ohne Datum)“."""
    return t("when.on", lang, d=day_text(r["_local"], lang)) if r["_local"] else t("when.ride", lang, n=r["ride_id"])


RIDE_BESTS = [
    # key, Gruppe, Feld, Einheit, Stellen, max/min, Mindest-km (Titel und Satz: ``best.<key>.title``/``.s`` in i18n)
    ("rider_kj", "rider", "rider_kj", "kJ", 0, max, MIN_KM_BEST),
    ("avg_rider_power", "rider", "avg_rider_power_w", "W", 0, max, MIN_KM_BEST),
    ("best_5min", "rider", "best_5min_w", "W", 0, max, MIN_KM_BEST),
    ("best_20min", "rider", "best_20min_w", "W", 0, max, MIN_KM_BEST),
    ("max_rider_power", "rider", "max_rider_power_w", "W", 0, max, MIN_KM_BEST),
    ("motor_share_max", "motor", "motor_share_pct", "%", 0, max, MIN_KM_BEST),
    ("motor_share_min", "motor", "motor_share_pct", "%", 0, min, MIN_KM_SELF),
    ("motor_wh", "motor", "motor_wh", "Wh", 0, max, MIN_KM_BEST),
    ("max_motor_power", "motor", "max_motor_power_w", "W", 0, max, MIN_KM_BEST),
    ("distance", "ride", "distance_km", "km", 1, max, MIN_KM_BEST),
    ("ascent", "ride", "ascent_m", "m", 0, max, MIN_KM_BEST),
    ("max_speed", "ride", "max_speed_kmh", "km/h", 1, max, MIN_KM_BEST),
    ("km_per_pct", "ride", "km_per_pct", "km je 1 % Akku", 2, max, MIN_KM_BEST),
]


def _value_text(value: float, unit: str, digits: int, lang: str = "de") -> str:
    return f"{num(value, digits, lang)} {unit}"


def ride_bests(rows: list[dict], lang: str = "de") -> list[dict]:
    out = []
    for key, group, field, unit, digits, pick, min_km in RIDE_BESTS:
        cands = [r for r in rows if r[field] is not None and r["distance_km"] >= min_km]
        if field == "km_per_pct":
            cands = [r for r in cands if (r["battery_used_pct"] or 0) >= MIN_BATTERY_PCT]
        if pick is max:
            cands = [r for r in cands if r[field] > 0]
        if not cands:
            continue
        best = pick(cands, key=lambda r: r[field])
        unit = t("unit.km_per_pct", lang) if unit == "km je 1 % Akku" else unit
        vt = _value_text(best[field], unit, digits, lang)
        out.append({
            "key": key, "group": group, "title": t(f"best.{key}.title", lang), "ride": best["id"],
            "date": best["_local"].isoformat() if best["_local"] else None,
            "value": best[field], "unit": unit, "value_text": vt, "when_text": _when(best, lang),
            "sentence": t(f"best.{key}.s", lang, when=_when(best, lang), v=vt),
        })
    return out


def period_bests(rows: list[dict], today: date, lang: str = "de") -> list[dict]:
    """Tag/Woche/Monat mit der meisten eigenen Energie und der längsten Strecke.

    Ein Zeitraum erscheint nur, wenn er mehr bringt als die nächstkleinere Stufe
    (sonst stünde dieselbe Zahl zweimal da, z. B. ein Tag mit nur einer Fahrt).
    """
    out = []
    dated = [r for r in rows if r["_local"]]
    finer = {
        "rider_kj": max((r["rider_kj"] or 0 for r in dated), default=0),
        "distance_km": max((r["distance_km"] for r in dated), default=0),
    }
    for period in ("day", "week", "month"):
        buckets: dict[date, dict] = {}
        for r in dated:
            _add(buckets.setdefault(period_start(r["_local"], period), _empty()), r)
        sums = {k: _finish(v) for k, v in buckets.items()}
        for field, unit, digits in (("rider_kj", "kJ", 0), ("distance_km", "km", 1)):
            if not sums:
                continue
            start = max(sums, key=lambda k: (sums[k][field], k))
            s = sums[start]
            value = s[field] or 0
            if round(value, digits) <= round(finer[field], digits):
                continue
            finer[field] = value
            vt = _value_text(value, unit, digits, lang)
            when = {"day": t("when.on", lang, d=day_text(start, lang)),
                    "week": t("when.week", lang, w=start.isocalendar()[1], y=start.isocalendar()[0]),
                    "month": t("when.month", lang, m=i18n.MONTHS[i18n.norm(lang)][start.month - 1], y=start.year)}[period]
            when_text = when if period == "day" else when + " · " + t("bestp.rides", lang, n=s["rides"])
            out.append({
                "when_text": when_text,
                "key": f"{period}_{field}", "group": "period", "period": period,
                "title": t(f"bestp.title.{field}", lang, t=t(f"bestp.title.{period}", lang)),
                "offset": steps_between(period_start(today, period), start, period),
                "label": period_title(start, period, lang), "value": value, "unit": unit, "value_text": vt, "rides": s["rides"],
                "sentence": t("bestp.sentence", lang, when=when, noun=t(f"bestp.noun.{period}", lang), what=t(f"bestp.what.{field}", lang), v=vt, n=s["rides"]),
            })
    return out


# ----------------------------------------------------------------- Ganzes


def load_rows(db, tz: ZoneInfo) -> list[dict]:
    rows = []
    for row in db.query(RIDE_SQL):
        r = dict(row)
        r["distance_km"] = (r["distance_m"] or 0) / 1000
        for key in ("assist", "gears", "dist", "by_gradient"):
            raw = r.pop(f"{key}_json", None)
            try:
                parsed = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                parsed = None
            r[key] = parsed if isinstance(parsed, dict) else None
        r["_local"] = datetime.fromisoformat(r["start_utc"]).astimezone(tz).date() if r["start_utc"] else None
        rows.append(r)
    return rows


DIST_KEYS = ("rider_power_s", "motor_power_s", "cadence_s", "speed_s", "gradient_s", "heart_rate_s")


def _gear_order(key: str):
    try:
        return (0, int(key), key)
    except ValueError:
        return (1, 0, key)


_LEVEL_OF = {name: level for level, name in ASSIST_LEVELS.items()}


def _mode_order(name: str):
    """Wie auf der Fahrtseite: nach Stufe (aus, eco, …, dann eigen_20, eigen_21, …)."""
    if name in _LEVEL_OF:
        return (_LEVEL_OF[name], name)
    tail = name.rsplit("_", 1)[-1]
    return (int(tail), name) if tail.isdigit() else (10_000, name)


def mix(rows: list[dict]) -> dict:
    """Verteilungen (Modi, Gänge, Leistung, …) über mehrere Fahrten aufsummiert.

    Fehlende Felder älterer Auswertungen zählen als leer. Mittelwerte je Modus sind
    zeitgewichtet (Tempo nach Bewegungszeit, Leistung nach Modus-Sekunden).
    """
    seconds_per_mode: dict[str, float] = {}
    acc: dict[str, dict] = {}
    changes = gear_changes = 0
    gears: dict[str, float] = {}
    moving_modes: dict[str, float] = {}
    moving_gears: dict[str, float] = {}
    dists: dict[str, dict[str, float] | None] = {k: None for k in DIST_KEYS}
    cad: dict[str, dict] = {}
    grad: dict[str, dict] = {}
    for r in rows:
        assist = r.get("assist") or {}
        for name, secs in (assist.get("seconds_per_mode") or {}).items():
            seconds_per_mode[name] = seconds_per_mode.get(name, 0) + (secs or 0)
        for name, m in (assist.get("per_mode") or {}).items():
            moving_modes[name] = moving_modes.get(name, 0) + (m.get("moving_seconds", m.get("seconds")) or 0)
            a = acc.setdefault(name, {"seconds": 0, "moving_seconds": 0, "distance_m": 0, "spd": 0.0, "spd_t": 0, "mot": 0.0, "mot_t": 0, "rid": 0.0, "rid_t": 0})
            secs, mv = m.get("seconds") or 0, m.get("moving_seconds") or 0
            a["seconds"] += secs
            a["moving_seconds"] += mv
            a["distance_m"] += m.get("distance_m") or 0
            for field, v, w in (("spd", m.get("avg_moving_speed_kmh"), mv), ("mot", m.get("avg_motor_power_w"), mv), ("rid", m.get("avg_rider_power_w"), mv)):
                if v is not None and w:
                    a[field] += v * w
                    a[field + "_t"] += w
        changes += assist.get("changes") or 0
        g = r.get("gears") or {}
        for gear, secs in (g.get("seconds_per_gear") or {}).items():
            gears[gear] = gears.get(gear, 0) + (secs or 0)
        for gear, m in (g.get("per_gear") or {}).items():
            moving_gears[gear] = moving_gears.get(gear, 0) + (m.get("moving_seconds", m.get("seconds")) or 0)
        for gear, m in (g.get("per_gear") or {}).items():
            c = cad.setdefault(gear, {"mv": 0, "rpm": 0.0, "rpm_t": 0})
            c["mv"] += m.get("moving_seconds", m.get("seconds")) or 0
            w = m.get("cadence_seconds", m.get("moving_seconds"))
            if m.get("avg_cadence_rpm") is not None and w:
                c["rpm"] += m["avg_cadence_rpm"] * w
                c["rpm_t"] += w
        for label, x in (r.get("by_gradient") or {}).items():
            b = grad.setdefault(label, {"seconds": 0, "rid": [0.0, 0], "mot": [0.0, 0], "spd": [0.0, 0]})
            secs = x.get("seconds") or 0
            b["seconds"] += secs
            for f, k in (("rid", "avg_rider_power_w"), ("mot", "avg_motor_power_w"), ("spd", "avg_speed_kmh")):
                if x.get(k) is not None and secs:
                    b[f][0] += x[k] * secs
                    b[f][1] += secs
        gear_changes += g.get("changes") or 0
        d = r.get("dist") or {}
        for key in DIST_KEYS:
            if d.get(key):
                tgt = dists[key] = dists[key] if dists[key] is not None else {}
                for label, secs in d[key].items():
                    tgt[label] = tgt.get(label, 0) + (secs or 0)
    per_mode = {}
    for name, a in acc.items():
        per_mode[name] = {
            "seconds": a["seconds"], "moving_seconds": a["moving_seconds"], "distance_m": round(a["distance_m"]),
            "avg_moving_speed_kmh": round(a["spd"] / a["spd_t"], 1) if a["spd_t"] else None,
            "avg_motor_power_w": round(a["mot"] / a["mot_t"]) if a["mot_t"] else None,
            "avg_rider_power_w": round(a["rid"] / a["rid_t"]) if a["rid_t"] else None,
        }
    by_gradient = {}
    for i in range(len(GRADIENT_BINS) + 1):
        b = grad.get(_label(GRADIENT_BINS, i))
        if b:
            by_gradient[_label(GRADIENT_BINS, i)] = {
                "seconds": b["seconds"],
                "avg_rider_power_w": round(b["rid"][0] / b["rid"][1]) if b["rid"][1] else None,
                "avg_motor_power_w": round(b["mot"][0] / b["mot"][1]) if b["mot"][1] else None,
                "avg_speed_kmh": round(b["spd"][0] / b["spd"][1], 1) if b["spd"][1] else None,
            }
    cad_per_gear = {k: round(c["rpm"] / c["rpm_t"]) for k, c in sorted(cad.items(), key=lambda kv: _gear_order(kv[0]))
                    if c["mv"] >= 30 and c["rpm_t"]}
    return {
        "rides": len(rows),
        "by_gradient": by_gradient,
        "assist": {"seconds_per_mode": dict(sorted(seconds_per_mode.items(), key=lambda kv: _mode_order(kv[0]))),
                   "per_mode": dict(sorted(per_mode.items(), key=lambda kv: _mode_order(kv[0]))), "changes": changes,
                   "moving_seconds_per_mode": dict(sorted(moving_modes.items(), key=lambda kv: _mode_order(kv[0])))},
        "gears": {"seconds_per_gear": dict(sorted(gears.items(), key=lambda kv: _gear_order(kv[0]))), "changes": gear_changes,
                  "moving_seconds_per_gear": dict(sorted(moving_gears.items(), key=lambda kv: _gear_order(kv[0]))),
                  "avg_cadence_per_gear": cad_per_gear},
        "distributions": dists,
    }


def _build_all(rows: list[dict], today: date, lang: str) -> dict:
    """Zeitraum „Gesamt“: alle datierten Fahrten, Balken je Jahr, kein Blättern, kein Vorzeitraum-Vergleich."""
    dated = [r["_local"] for r in rows if r["_local"]]
    this_year = period_start(today, "year")
    first_year = period_start(min(dated), "year") if dated else this_year
    n = min(PERIODS["all"], max(steps_between(this_year, first_year, "year") + 1, MIN_BUCKETS["year"]))
    starts = [shift(this_year, "year", i - n + 1) for i in range(n)]
    index = {s: i for i, s in enumerate(starts)}
    accs = [_empty() for _ in starts]
    all_acc = _empty()
    for r in rows:
        if r["_local"] is None:
            continue
        _add(all_acc, r)
        i = index.get(period_start(r["_local"], "year"))
        if i is not None:
            _add(accs[i], r)
    series = [{"key": period_key(s, "year"), "label": period_label(s, "year", lang), "title": period_title(s, "year", lang),
               "start": s.isoformat(), "end": period_end(s, "year").isoformat(), **_finish(acc)} for s, acc in zip(starts, accs)]
    cur = _finish(all_acc)
    title = t("title.all", lang, d=i18n.date_text(min(dated), lang)) if dated else t("cur.all", lang)
    cur.update(title=title, label=t("cur.all", lang))
    name = t("cur.all", lang)
    cmp = {k: {"current": cur.get(k), "previous": None, "delta": None, "delta_pct": None} for k in COMPARE_KEYS}
    total_acc = _empty()
    for r in rows:
        _add(total_acc, r)
    total = _finish(total_acc)
    total["first_date"] = min(dated).isoformat() if dated else None
    total["last_date"] = max(dated).isoformat() if dated else None
    total["undated_rides"] = sum(1 for r in rows if r["_local"] is None)
    real = [r for r in rows if r["distance_km"] >= MIN_KM_BEST]
    recent = [{
        "id": r["id"], "ride_id": r["ride_id"], "start_utc": r["start_utc"],
        "date": r["_local"].isoformat() if r["_local"] else None,
        "distance_km": round(r["distance_km"], 2), "moving_time_s": r["moving_time_s"], "ascent_m": r["ascent_m"],
        "rider_kj": r["rider_kj"], "motor_wh": r["motor_wh"], "motor_share_pct": r["motor_share_pct"],
    } for r in rows[:5]]
    return {
        "period": "all", "offset": 0, "max_offset": 0, "last_offset": 0 if dated else None,
        "today": today.isoformat(),
        "current": {**cur, "name": name},
        "previous": {**_finish(_empty()), "name": t("period.before", lang)},
        "versus": "",
        "comparison": cmp,
        "headline": t("headline", lang, name=name, km=num(cur["distance_km"], 1, lang)),
        "series": series,
        "mix": mix([r for r in rows if r["_local"]]),
        "highlights": ride_bests(real, lang) + period_bests(real, today, lang),
        "total": total,
        "recent": recent,
    }


def build(rows: list[dict], period: str, offset: int, today: date, lang: str = "de") -> dict:
    if period not in PERIODS:
        raise ValueError(f"Unbekannter Zeitraum: {period}")
    if period == "all":
        return _build_all(rows, today, lang)
    dated = [r["_local"] for r in rows if r["_local"]]
    # Ältester sinnvoller Offset: der Zeitraum der ersten Fahrt (hält auch Datumsrechnung im gültigen Bereich)
    oldest = max(0, steps_between(period_start(today, period), period_start(min(dated), period), period)) if dated else 0
    offset = min(max(0, int(offset)), oldest)
    n = PERIODS[period]
    current = shift(period_start(today, period), period, -offset)
    # Diagramm beginnt im Zeitraum der ersten Fahrt (höchstens n, mindestens MIN_BUCKETS Zeiträume)
    back = steps_between(current, period_start(min(dated), period), period) if dated else 0
    n = min(n, max(back + 1, MIN_BUCKETS[period]))
    starts = [shift(current, period, i - n + 1) for i in range(n)]
    index = {s: i for i, s in enumerate(starts)}
    accs = [_empty() for _ in starts]
    prev_start = shift(current, period, -1)
    for r in rows:
        if r["_local"] is None:
            continue
        i = index.get(period_start(r["_local"], period))
        if i is not None:
            _add(accs[i], r)
    series = []
    for s, acc in zip(starts, accs):
        series.append({"key": period_key(s, period), "label": period_label(s, period, lang), "title": period_title(s, period, lang),
                       "start": s.isoformat(), "end": period_end(s, period).isoformat(), **_finish(acc)})
    cur, prev = series[-1], series[-2]

    if offset == 0:
        cur_name = t(f"cur.{period}", lang)
    elif offset == 1:
        cur_name = t(f"last.{period}", lang)
    else:
        cur_name = period_title(current, period, lang)

    total_acc = _empty()
    for r in rows:
        _add(total_acc, r)
    total = _finish(total_acc)
    total["first_date"] = min(dated).isoformat() if dated else None
    total["last_date"] = max(dated).isoformat() if dated else None
    total["undated_rides"] = sum(1 for r in rows if r["_local"] is None)

    real = [r for r in rows if r["distance_km"] >= MIN_KM_BEST]
    recent = [{
        "id": r["id"], "ride_id": r["ride_id"], "start_utc": r["start_utc"],
        "date": r["_local"].isoformat() if r["_local"] else None,
        "distance_km": round(r["distance_km"], 2), "moving_time_s": r["moving_time_s"], "ascent_m": r["ascent_m"],
        "rider_kj": r["rider_kj"], "motor_wh": r["motor_wh"], "motor_share_pct": r["motor_share_pct"],
    } for r in rows[:5]]

    in_period = [r for r in rows if r["_local"] and period_start(r["_local"], period) == current]
    cmp = compare(cur, prev)
    d = cmp["distance_km"]
    versus = t(f"versus.{period}", lang)
    headline = t("headline", lang, name=cur_name, km=num(cur["distance_km"], 1, lang))
    if d["delta_pct"] is not None:
        headline += t("headline.delta", lang, sign="+" if d["delta_pct"] >= 0 else "−", p=abs(d["delta_pct"]), versus=versus)

    return {
        "period": period,
        "offset": offset,
        "max_offset": oldest,
        # Zeitraum der letzten Fahrt (für „Zur letzten Fahrt springen“ bei leerem Zeitraum)
        "last_offset": max(0, steps_between(period_start(today, period), period_start(max(dated), period), period)) if dated else None,
        "today": today.isoformat(),
        "current": {**cur, "name": cur_name},
        "previous": {**prev, "name": t("period.before", lang)},
        "versus": versus,
        "comparison": cmp,
        "headline": headline,
        "series": series,
        "mix": mix(in_period),
        "highlights": ride_bests(real, lang) + period_bests(real, today, lang),
        "total": total,
        "recent": recent,
    }
