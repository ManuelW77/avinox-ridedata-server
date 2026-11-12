"""Weitergehende Auswertung einer Fahrt für Webansicht und Export.

``analyze()`` liefert ein JSON-taugliches dict: Kennzahlen (wie App), Zeit je
Unterstützungsstufe und Gang, Verteilungen, Bestleistungen, Akku, Stopps,
Zeitreihe für Diagramme, GPS-Infos und – falls vorhanden – die passenden
Werte aus der Nutzungsstatistik (``buried_rec``).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import fields

from .buried import RideStats, Session, match_ride
from .ride_rec import Ride, Sample
from .summary import MOVING_MIN_KMH, _intervals, summarize
from .track import bounds, track_length_m, track_points

ASSIST_LEVELS = {0: "aus", 1: "eco", 2: "trail", 3: "turbo", 4: "auto", 5: "boost"}
"""Zuordnung Feld 8 -> Modus, abgeglichen mit ``assist_mode_usage`` (3 = turbo vermutet).

Ab 20 folgen die in der App selbst angelegten Modi. Ihre Namen stehen nicht auf
dem Laufwerk; sie heißen ``eigen_<nummer>``, solange ``custom_names`` sie nicht benennt.
"""
CUSTOM_MODE_MIN = 20

POWER_BINS = [1, 50, 100, 150, 200, 300, 400, 600, 800, 1000]
CADENCE_BINS = [1, 40, 60, 70, 80, 90, 100, 120]
SPEED_BINS = [1, 5, 10, 15, 20, 25, 30, 40]
GRADIENT_BINS = [-10, -6, -3, -1, 1, 3, 6, 10]
HEART_RATE_BINS = [1, 100, 120, 140, 160, 180]
BEST_EFFORT_S = [5, 10, 30, 60, 300, 600, 1200, 3600]
STOP_MIN_S = 30
"""Stillstand ab dieser Dauer zählt als Stopp."""
SERIES_MAX_POINTS = 1000


def assist_name(level: int, custom_names: dict[int, str] | None = None) -> str:
    if custom_names and level in custom_names:
        return custom_names[level]
    if level in ASSIST_LEVELS:
        return ASSIST_LEVELS[level]
    return f"eigen_{level}" if level >= CUSTOM_MODE_MIN else f"stufe_{level}"


def _label(bins: list[float], i: int) -> str:
    if i == 0:
        return f"<{bins[0]:g}"
    if i == len(bins):
        return f"≥{bins[-1]:g}"
    return f"{bins[i - 1]:g}–{bins[i]:g}"


def histogram(values: list[float], weights: list[int], bins: list[float]) -> dict[str, int]:
    """Sekunden je Bereich. Ein Wert fällt in den Bereich [bins[i-1], bins[i])."""
    out = {_label(bins, i): 0 for i in range(len(bins) + 1)}
    for v, w in zip(values, weights):
        if w <= 0 or v is None:
            continue
        i = sum(1 for b in bins if v >= b)
        out[_label(bins, i)] += w
    return out


def best_efforts(values: list[float], durations: list[int] = BEST_EFFORT_S) -> dict[str, float]:
    """Höchster Durchschnitt über ``d`` aufeinanderfolgende Samples (≈ Sekunden)."""
    out: dict[str, float] = {}
    n = len(values)
    prefix = [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
    for d in durations:
        if d > n:
            break
        best = max((prefix[i + d] - prefix[i]) for i in range(n - d + 1)) / d
        out[f"{d}s"] = round(best, 1)
    return out


def _stops(samples: list[Sample], dts: list[int]) -> list[dict]:
    stops, start, length = [], None, 0
    t = 0
    for i, (s, dt) in enumerate(zip(samples, dts)):
        if s.speed_kmh < MOVING_MIN_KMH:
            if start is None:
                start, length = (i, t), 0
            length += dt
        else:
            if start is not None and length >= STOP_MIN_S and start[0] > 0:
                stops.append({"index": start[0], "offset_s": start[1], "duration_s": length, "distance_m": samples[start[0]].distance_m})
            start = None
        t += dt
    return stops


def _series(samples: list[Sample], dts: list[int], max_points: int) -> dict:
    """Zeitreihe in gleich großen Blöcken gemittelt (Stufe/Gang: häufigster Wert)."""
    n = len(samples)
    if not n:
        return {"bucket_s": 0, "fields": [], "rows": []}
    size = max(1, math.ceil(n / max_points))
    fields = ["offset_s", "distance_m", "altitude_m", "speed_kmh", "rider_power_w", "motor_power_w", "cadence_rpm",
              "heart_rate_bpm", "battery_pct", "gradient_pct", "temperature_c", "assist_level", "gear"]
    rows = []
    offset = 0
    offsets = []
    for dt in dts:
        offsets.append(offset)
        offset += dt

    def avg(chunk: list[Sample], attr: str, digits: int) -> float | None:
        vals = [getattr(s, attr) for s in chunk if getattr(s, attr) is not None]
        return round(sum(vals) / len(vals), digits) if vals else None

    for start in range(0, n, size):
        chunk = samples[start : start + size]
        rows.append([
            offsets[start],
            chunk[-1].distance_m,
            avg(chunk, "altitude_m", 1),
            avg(chunk, "speed_kmh", 1),
            avg(chunk, "rider_power_w", 0),
            avg(chunk, "motor_power_w", 0),
            avg(chunk, "cadence_rpm", 0),
            avg(chunk, "heart_rate_bpm", 0),
            chunk[-1].battery_pct,
            avg(chunk, "gradient_pct", 1),
            avg(chunk, "temperature_c", 1),
            Counter(s.assist_level for s in chunk).most_common(1)[0][0],
            Counter(s.gear for s in chunk).most_common(1)[0][0],
        ])
    return {"bucket_s": size, "fields": fields, "rows": rows}


def _per_level(samples: list[Sample], dts: list[int], key) -> dict:
    groups: dict[object, list[tuple[Sample, int]]] = {}
    for s, dt in zip(samples, dts):
        groups.setdefault(key(s), []).append((s, dt))
    out = {}
    for k, items in sorted(groups.items(), key=lambda kv: kv[0]):
        secs = sum(dt for _, dt in items)
        moving = [(s, dt) for s, dt in items if s.speed_kmh >= MOVING_MIN_KMH]
        mt = sum(dt for _, dt in moving)
        out[str(k)] = {
            "seconds": secs,
            "moving_seconds": mt,
            "distance_m": round(sum(s.speed_kmh / 3.6 * dt for s, dt in items)),
            "avg_moving_speed_kmh": round(sum(s.speed_kmh * dt for s, dt in moving) / mt, 1) if mt else None,
            "avg_motor_power_w": round(sum(s.motor_power_w * dt for s, dt in items) / secs) if secs else None,
            "avg_rider_power_w": round(sum(s.rider_power_w * dt for s, dt in items) / secs) if secs else None,
        }
    return out


def _changes(values: list[int]) -> int:
    return sum(1 for a, b in zip(values, values[1:]) if a != b and a and b)


def _ride_stats_dict(stats: RideStats) -> dict:
    d = {f.name: getattr(stats, f.name) for f in fields(stats) if f.name != "session"}
    d["end_time"] = stats.end_time.isoformat() if stats.end_time else None
    session = stats.session
    if session:
        d["session"] = {
            "boot_count": session.boot_count,
            "power_on_s": session.power_on_s,
            "firmware": session.firmware,
            "battery": session.battery,
            "battery_temp_s": session.battery_temp_s,
            "motor_electronics_temp_s": session.motor_electronics_temp_s,
            "thermal_limit": session.thermal_limit,
            "pitch_km": session.pitch_km,
            "roll_km": session.roll_km,
            "controller_presses": session.controller_presses,
            "assist_mode_settings": session.assist_mode_settings,
            "rides_in_session": len(session.rides),
        }
    return d


def analyze(
    ride: Ride,
    sessions: list[Session] | None = None,
    series_points: int = SERIES_MAX_POINTS,
    custom_names: dict[int, str] | None = None,
) -> dict:
    """``custom_names``: Namen für eigene Modi, z. B. ``{21: "Tour"}``."""
    s = ride.samples
    dts = _intervals(s)
    summary = summarize(ride)
    moving_w = [dt if x.speed_kmh >= MOVING_MIN_KMH else 0 for x, dt in zip(s, dts)]

    rider_kj = summary.rider_energy_kj
    motor_kj = summary.motor_energy_wh * 3.6
    battery = [x.battery_pct for x in s if x.battery_pct is not None]
    used = (battery[0] - battery[-1]) if battery else None
    km = summary.distance_km
    temps = [x.temperature_c for x in s if x.temperature_c is not None]
    press = [x.pressure_kpa for x in s if x.pressure_kpa is not None]
    alts = [x.altitude_m for x in s if x.altitude_m is not None]
    hr = [x.heart_rate_bpm for x in s if x.heart_rate_bpm]

    points = track_points(ride)
    box = bounds(points)
    per_mode = {assist_name(int(k), custom_names): v for k, v in _per_level(s, dts, lambda x: x.assist_level).items()}
    per_gear = {k: v for k, v in _per_level(s, dts, lambda x: x.gear).items() if k != "0"}

    result = {
        "ride_id": ride.header.ride_id,
        "summary": summary.to_dict(),
        "warnings": list(ride.warnings),
        "assist": {
            "seconds_per_mode": {k: v["seconds"] for k, v in per_mode.items()},
            "per_mode": per_mode,
            "changes": _changes([x.assist_level for x in s]),
        },
        "gears": {
            "seconds_per_gear": {k: v["seconds"] for k, v in per_gear.items()},
            "per_gear": per_gear,
            "changes": _changes([x.gear for x in s]),
        },
        "distributions": {
            "rider_power_s": histogram([x.rider_power_w for x in s], moving_w, POWER_BINS),
            "motor_power_s": histogram([x.motor_power_w for x in s], moving_w, POWER_BINS),
            "cadence_s": histogram([x.cadence_rpm for x in s], moving_w, CADENCE_BINS),
            "speed_s": histogram([x.speed_kmh for x in s], dts, SPEED_BINS),
            "gradient_s": histogram([x.gradient_pct for x in s], moving_w, GRADIENT_BINS),
            "heart_rate_s": histogram([x.heart_rate_bpm for x in s], dts, HEART_RATE_BINS) if hr else None,
        },
        "best_efforts": {
            "rider_power_w": best_efforts([x.rider_power_w for x in s]),
            "motor_power_w": best_efforts([x.motor_power_w for x in s]),
            "total_power_w": best_efforts([x.total_power_w for x in s]),
            "speed_kmh": best_efforts([x.speed_kmh for x in s]),
        },
        "energy": {
            "rider_kj": round(rider_kj, 1),
            "motor_kj": round(motor_kj, 1),
            "motor_wh": round(summary.motor_energy_wh, 1),
            "motor_share_pct": round(100 * motor_kj / (rider_kj + motor_kj), 1) if rider_kj + motor_kj else None,
            "calories_kcal": round(summary.calories_kcal),
        },
        "battery": {
            "start_pct": battery[0] if battery else None,
            "end_pct": battery[-1] if battery else None,
            "used_pct": used,
            "pct_per_km": round(used / km, 2) if used and km else None,
            "km_per_pct": round(km / used, 2) if used and km else None,
            "pct_per_100m_ascent": round(100 * used / summary.ascent_m, 2) if used and summary.ascent_m else None,
            "motor_wh_per_km": round(summary.motor_energy_wh / km, 1) if km else None,
            "motor_wh_per_pct": round(summary.motor_energy_wh / used, 2) if used and used >= 5 else None,
        },
        "environment": {
            "temperature_min_c": min(temps, default=None),
            "temperature_max_c": max(temps, default=None),
            "temperature_avg_c": round(sum(temps) / len(temps), 1) if temps else None,
            "pressure_min_kpa": min(press, default=None),
            "pressure_max_kpa": max(press, default=None),
        },
        "altitude": {
            "min_m": min(alts, default=None),
            "max_m": max(alts, default=None),
            "start_m": alts[0] if alts else None,
            "end_m": alts[-1] if alts else None,
            "samples_ascent_m": summary.samples_ascent_m,
            "samples_descent_m": summary.samples_descent_m,
        },
        "stops": _stops(s, dts),
        "gps": {
            "points": len(points),
            "coverage_pct": round(100 * sum(1 for x in s if x.latitude is not None) / len(s), 1) if s else 0,
            "length_m": round(track_length_m(points)) if points else 0,
            "bbox": list(box) if box else None,
            "preview": [list(p) for p in ride.header.preview],
        },
        "series": _series(s, dts, series_points),
        "device_stats": None,
    }
    if sessions:
        stats = match_ride(ride, sessions)
        if stats:
            result["device_stats"] = _ride_stats_dict(stats)
    return result
