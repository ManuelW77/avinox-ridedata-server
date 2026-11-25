"""Mehrere Fahrten zu einer zusammenfassen (Display legt nach jedem Ausschalten eine neue an).

``merge_rides`` baut aus geparsten Fahrten eine künstliche ``Ride``: Samples der Reihe nach,
Streckenzähler und Kalorien fortlaufend. Die Pausen zwischen den Teilen bleiben als
Zeitsprung stehen; Auswertung und Export behandeln Sprünge über ``MAX_GAP_S`` bereits als
Pause (zählt weder als Fahrzeit noch als Strecke).
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime

from .buried import RideStats
from .ride_rec import HeaderStats, Ride
from .summary import calories_offset, ride_calories

GAP_SUGGEST_S = 3600
"""Pause unter dieser Dauer: aufeinanderfolgende Fahrten gelten als Kandidaten zum Zusammenführen."""


def merge_rides(rides: list[Ride]) -> Ride:
    """``rides`` zeitlich sortiert (mind. eine). Gibt eine neue Ride zurück, die Eingaben bleiben unverändert."""
    if not rides:
        raise ValueError("keine Fahrten")
    first, last = rides[0], rides[-1]
    counts = [r.header.sample_count or len(r.samples) for r in rides]
    total = sum(counts) or 1

    stats: dict[str, float] = {}
    for f in fields(HeaderStats):
        vals = [getattr(r.header.stats, f.name) for r in rides]
        if f.name.startswith("max_"):
            stats[f.name] = max(vals)
        elif f.name == "calories_kcal":
            stats[f.name] = sum(ride_calories(r) for r in rides)
        else:
            stats[f.name] = sum(v * n for v, n in zip(vals, counts)) / total

    samples = []
    dist_off = 0
    cal_off = 0.0
    for r in rides:
        base = calories_offset(r.samples)
        for x in r.samples:
            cal = None if x.calories_kcal is None else cal_off + max(0.0, x.calories_kcal - base)
            samples.append(replace(x, distance_m=x.distance_m + dist_off, calories_kcal=cal))
        dist_off += r.header.distance_m
        cal_off += ride_calories(r)

    header = replace(
        first.header,
        end=last.header.end,
        distance_m=dist_off,
        sample_count=sum(counts),
        ascent_m=sum(r.header.ascent_m for r in rides),
        descent_m=sum(r.header.descent_m for r in rides),
        stats=HeaderStats(**stats),
    )
    warnings = [w for r in rides for w in r.warnings]
    return Ride(header=header, samples=samples, warnings=warnings)


def suggest_pairs(items: list[dict]) -> dict[int, int]:
    """Kandidaten: ``{id: id der vorherigen Fahrt}``, wenn die Pause < 60 min ist.

    ``items``: ``id``, ``start`` (ISO-UTC oder None), ``elapsed_s``. Fahrten ohne Start zählen nicht.
    Überlappungen (Pause < 0) gelten nicht als Kandidat: das wäre keine Folgefahrt.
    """
    dated = sorted((i for i in items if i.get("start")), key=lambda i: (i["start"], i["id"]))
    out: dict[int, int] = {}
    for prev, cur in zip(dated, dated[1:]):
        end = datetime.fromisoformat(prev["start"]).timestamp() + (prev.get("elapsed_s") or 0)
        gap = datetime.fromisoformat(cur["start"]).timestamp() - end
        if -60 <= gap < GAP_SUGGEST_S:
            out[cur["id"]] = prev["id"]
    return out


def _sum_opt(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _weighted(parts: list[RideStats], name: str):
    pairs = [(getattr(p, name), p.duration_s) for p in parts if getattr(p, name) is not None]
    total = sum(w for _, w in pairs)
    if not pairs:
        return None
    if total <= 0:
        return sum(v for v, _ in pairs) / len(pairs)
    return sum(v * w for v, w in pairs) / total


def _max_opt(parts: list[RideStats], name: str):
    vals = [getattr(p, name) for p in parts if getattr(p, name) is not None]
    return max(vals) if vals else None


def _sum_dicts(dicts: list[dict]) -> dict:
    out: dict = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return out


def combine_device_stats(parts: list[RideStats]) -> RideStats:
    """Display-Statistik mehrerer Teilfahrten (zeitlich sortiert) zu einer zusammenfassen."""
    if not parts:
        raise ValueError("keine Statistik")
    duration = sum(p.duration_s for p in parts)
    distance = sum(p.distance_km for p in parts)
    return RideStats(
        end_time=parts[-1].end_time,
        duration_s=duration,
        distance_km=distance,
        avg_speed_kmh=distance / duration * 3600 if duration else 0.0,
        elev_gain_m=sum(p.elev_gain_m for p in parts),
        elev_start_m=parts[0].elev_start_m,
        battery_used_pct=_sum_opt(p.battery_used_pct for p in parts),
        avg_cadence_rpm=_weighted(parts, "avg_cadence_rpm"),
        max_cadence_rpm=_max_opt(parts, "max_cadence_rpm"),
        avg_rider_torque_nm=_weighted(parts, "avg_rider_torque_nm"),
        max_rider_torque_nm=_max_opt(parts, "max_rider_torque_nm"),
        avg_heart_rate_bpm=_weighted(parts, "avg_heart_rate_bpm"),
        max_heart_rate_bpm=_max_opt(parts, "max_heart_rate_bpm"),
        seconds_per_gear=_sum_dicts([p.seconds_per_gear for p in parts]),
        seconds_per_assist_mode=_sum_dicts([p.seconds_per_assist_mode for p in parts]),
        assist_mode_changes=_sum_opt(p.assist_mode_changes for p in parts),
        boost_s=_sum_opt(p.boost_s for p in parts),
        motor_torque_s=_sum_dicts([p.motor_torque_s for p in parts]),
        power_s=_sum_dicts([p.power_s for p in parts]),
        session=parts[-1].session,
    )
