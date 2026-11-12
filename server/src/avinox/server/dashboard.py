"""Übersicht über alle Fahrten: Zeitreihen je Tag/Woche/Monat/Jahr, Vergleich und Bestwerte.

Gelesen wird nur aus der Tabelle ``rides`` (plus wenigen Feldern aus
``analysis_json`` per ``json_extract``) – die Rohdateien bleiben unangetastet.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

PERIODS: dict[str, int] = {"day": 30, "week": 26, "month": 24, "year": 5}
"""Zeitraum -> Anzahl der Balken in der Zeitreihe."""

MIN_KM_BEST = 1.0
"""Kürzere Fahrten (Testfahrten) zählen nicht für Bestwerte."""
MIN_KM_SELF = 2.0
"""„Am meisten selbst getreten“ erst ab dieser Strecke – sonst gewinnt jede Rollerei."""
MIN_BATTERY_PCT = 5
"""„Sparsamste Fahrt“ nur bei mindestens so viel Akkuverbrauch (sonst rundungsgetrieben)."""

WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")
MONTHS_SHORT = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")

CURRENT = {"day": "Heute", "week": "Diese Woche", "month": "Dieser Monat", "year": "Dieses Jahr"}
LAST = {"day": "Gestern", "week": "Letzte Woche", "month": "Letzter Monat", "year": "Letztes Jahr"}
VERSUS = {"day": "ggü. Vortag", "week": "ggü. Vorwoche", "month": "ggü. Vormonat", "year": "ggü. Vorjahr"}

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
       json_extract(analysis_json, '$.best_efforts.rider_power_w."1200s"') AS best_20min_w
FROM rides
ORDER BY start_utc IS NULL, start_utc DESC, path DESC
"""


# ----------------------------------------------------------------- Formatierung


def num(value: float | None, digits: int = 0) -> str:
    """Deutsche Zahl: 1.234,5"""
    if value is None:
        return "–"
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def emphasize(sentence: str, value_text: str) -> Markup:
    """Satz als HTML, der Wert darin fett."""
    safe = escape(sentence)
    return Markup(safe.replace(escape(value_text), Markup("<strong>") + escape(value_text) + Markup("</strong>"), 1))


def day_text(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m.%Y}"


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


def period_label(start: date, period: str) -> str:
    """Kurz für die Diagrammachse."""
    if period == "day":
        return f"{start.day}.{start.month}."
    if period == "week":
        return f"KW {start.isocalendar()[1]}"
    if period == "month":
        return f"{MONTHS_SHORT[start.month - 1]} {start:%y}"
    return str(start.year)


def period_title(start: date, period: str) -> str:
    """Ausführlich, z. B. für Überschriften und Sätze."""
    if period == "day":
        return day_text(start)
    if period == "week":
        y, w, _ = start.isocalendar()
        end = start + timedelta(days=6)
        return f"KW {w}/{y} ({start:%d.%m.}–{end:%d.%m.%Y})"
    if period == "month":
        return f"{MONTHS[start.month - 1]} {start.year}"
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


def _when(r: dict) -> str:
    """Satzanfang: „Am Do 05.11.2026“ bzw. „Bei Fahrt 7 (ohne Datum)“."""
    return f"Am {day_text(r['_local'])}" if r["_local"] else f"Bei Fahrt {r['ride_id']} (ohne Datum)"


RIDE_BESTS = [
    # key, Gruppe, Titel, Feld, Einheit, Stellen, max/min, Mindest-km, Satz
    ("rider_kj", "rider", "Meiste Eigenleistung", "rider_kj", "kJ", 0, max, MIN_KM_BEST,
     "{when} hattest du die meiste Eigenleistung: {v}."),
    ("avg_rider_power", "rider", "Höchste Ø-Leistung", "avg_rider_power_w", "W", 0, max, MIN_KM_BEST,
     "{when} bist du mit der höchsten Durchschnittsleistung gefahren: {v}."),
    ("best_5min", "rider", "Stärkste 5 Minuten", "best_5min_w", "W", 0, max, MIN_KM_BEST,
     "{when} hast du deine stärksten 5 Minuten getreten: {v} im Schnitt."),
    ("best_20min", "rider", "Stärkste 20 Minuten", "best_20min_w", "W", 0, max, MIN_KM_BEST,
     "{when} hast du deine stärksten 20 Minuten getreten: {v} im Schnitt."),
    ("max_rider_power", "rider", "Höchste Spitzenleistung", "max_rider_power_w", "W", 0, max, MIN_KM_BEST,
     "{when} hast du am kräftigsten in die Pedale getreten: {v} Spitze."),
    ("motor_share_max", "motor", "Motor hat am meisten geholfen", "motor_share_pct", "%", 0, max, MIN_KM_BEST,
     "{when} hat der Motor am meisten geholfen: {v} der Energie kamen von ihm."),
    ("motor_share_min", "motor", "Am meisten selbst getreten", "motor_share_pct", "%", 0, min, MIN_KM_SELF,
     "{when} hast du am meisten selbst getreten: nur {v} Motoranteil."),
    ("motor_wh", "motor", "Meiste Motorenergie", "motor_wh", "Wh", 0, max, MIN_KM_BEST,
     "{when} hat der Motor am meisten Energie geliefert: {v}."),
    ("max_motor_power", "motor", "Höchste Motorleistung", "max_motor_power_w", "W", 0, max, MIN_KM_BEST,
     "{when} hat der Motor am kräftigsten geschoben: {v} Spitze."),
    ("distance", "ride", "Längste Fahrt", "distance_km", "km", 1, max, MIN_KM_BEST,
     "{when} bist du am weitesten gefahren: {v}."),
    ("ascent", "ride", "Meiste Höhenmeter", "ascent_m", "m", 0, max, MIN_KM_BEST,
     "{when} hast du die meisten Höhenmeter gesammelt: {v}."),
    ("max_speed", "ride", "Höchstes Tempo", "max_speed_kmh", "km/h", 1, max, MIN_KM_BEST,
     "{when} warst du am schnellsten unterwegs: {v}."),
    ("km_per_pct", "ride", "Sparsamste Fahrt", "km_per_pct", "km je 1 % Akku", 2, max, MIN_KM_BEST,
     "{when} bist du am sparsamsten gefahren: {v}."),
]


def _value_text(value: float, unit: str, digits: int) -> str:
    return f"{num(value, digits)} {unit}" if unit != "%" else f"{num(value, digits)} %"


def ride_bests(rows: list[dict]) -> list[dict]:
    out = []
    for key, group, title, field, unit, digits, pick, min_km, sentence in RIDE_BESTS:
        cands = [r for r in rows if r[field] is not None and r["distance_km"] >= min_km]
        if field == "km_per_pct":
            cands = [r for r in cands if (r["battery_used_pct"] or 0) >= MIN_BATTERY_PCT]
        if pick is max:
            cands = [r for r in cands if r[field] > 0]
        if not cands:
            continue
        best = pick(cands, key=lambda r: r[field])
        vt = _value_text(best[field], unit, digits)
        out.append({
            "key": key, "group": group, "title": title, "ride": best["id"],
            "date": best["_local"].isoformat() if best["_local"] else None,
            "value": best[field], "unit": unit, "value_text": vt,
            "sentence": sentence.format(when=_when(best), v=vt),
        })
    return out


def period_bests(rows: list[dict], today: date) -> list[dict]:
    """Tag/Woche/Monat mit der meisten Eigenleistung und der längsten Strecke.

    Ein Zeitraum erscheint nur, wenn er mehr bringt als die nächstkleinere Stufe
    (sonst stünde dieselbe Zahl zweimal da, z. B. ein Tag mit nur einer Fahrt).
    """
    out = []
    dated = [r for r in rows if r["_local"]]
    finer = {
        "rider_kj": max((r["rider_kj"] or 0 for r in dated), default=0),
        "distance_km": max((r["distance_km"] for r in dated), default=0),
    }
    for period, noun in (("day", "an einem Tag"), ("week", "in einer Woche"), ("month", "in einem Monat")):
        buckets: dict[date, dict] = {}
        for r in dated:
            _add(buckets.setdefault(period_start(r["_local"], period), _empty()), r)
        sums = {k: _finish(v) for k, v in buckets.items()}
        for field, unit, digits, what in (("rider_kj", "kJ", 0, "am meisten selbst geleistet"),
                                          ("distance_km", "km", 1, "die längste Strecke geschafft")):
            if not sums:
                continue
            start = max(sums, key=lambda k: (sums[k][field], k))
            s = sums[start]
            value = s[field] or 0
            if round(value, digits) <= round(finer[field], digits):
                continue
            finer[field] = value
            vt = _value_text(value, unit, digits)
            when = {"day": f"Am {day_text(start)}", "week": f"In KW {start.isocalendar()[1]}/{start.isocalendar()[0]}",
                    "month": f"Im {MONTHS[start.month - 1]} {start.year}"}[period]
            out.append({
                "key": f"{period}_{field}", "group": "period", "period": period,
                "title": {"day": "Bester Tag", "week": "Beste Woche", "month": "Bester Monat"}[period]
                + (" · Eigenleistung" if field == "rider_kj" else " · Strecke"),
                "offset": steps_between(period_start(today, period), start, period),
                "label": period_title(start, period), "value": value, "unit": unit, "value_text": vt, "rides": s["rides"],
                "sentence": f"{when} hast du {noun} {what}: {vt} bei {s['rides']} Fahrten.",
            })
    return out


# ----------------------------------------------------------------- Ganzes


def load_rows(db, tz: ZoneInfo) -> list[dict]:
    rows = []
    for row in db.query(RIDE_SQL):
        r = dict(row)
        r["distance_km"] = (r["distance_m"] or 0) / 1000
        r["_local"] = datetime.fromisoformat(r["start_utc"]).astimezone(tz).date() if r["start_utc"] else None
        rows.append(r)
    return rows


def build(rows: list[dict], period: str, offset: int, today: date) -> dict:
    if period not in PERIODS:
        raise ValueError(f"Unbekannter Zeitraum: {period}")
    dated = [r["_local"] for r in rows if r["_local"]]
    # Ältester sinnvoller Offset: der Zeitraum der ersten Fahrt (hält auch Datumsrechnung im gültigen Bereich)
    oldest = max(0, steps_between(period_start(today, period), period_start(min(dated), period), period)) if dated else 0
    offset = min(max(0, int(offset)), oldest)
    n = PERIODS[period]
    current = shift(period_start(today, period), period, -offset)
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
        series.append({"key": period_key(s, period), "label": period_label(s, period), "title": period_title(s, period),
                       "start": s.isoformat(), "end": period_end(s, period).isoformat(), **_finish(acc)})
    cur, prev = series[-1], series[-2]

    if offset == 0:
        cur_name = CURRENT[period]
    elif offset == 1:
        cur_name = LAST[period]
    else:
        cur_name = period_title(current, period)

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

    cmp = compare(cur, prev)
    d = cmp["distance_km"]
    headline = f"{cur_name}: {num(cur['distance_km'], 1)} km"
    if d["delta_pct"] is not None:
        headline += f", {'+' if d['delta_pct'] >= 0 else '−'}{abs(d['delta_pct'])} % {VERSUS[period]}"

    return {
        "period": period,
        "offset": offset,
        "max_offset": oldest,
        # Zeitraum der letzten Fahrt (für „Zur letzten Fahrt springen“ bei leerem Zeitraum)
        "last_offset": max(0, steps_between(period_start(today, period), period_start(max(dated), period), period)) if dated else None,
        "today": today.isoformat(),
        "current": {**cur, "name": cur_name},
        "previous": {**prev, "name": "Zeitraum davor"},
        "versus": VERSUS[period],
        "comparison": cmp,
        "headline": headline,
        "series": series,
        "highlights": ride_bests(real) + period_bests(real, today),
        "total": total,
        "recent": recent,
    }
