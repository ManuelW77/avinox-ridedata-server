"""Mehrere Fahrten zu einer zusammenfassen (Display legt nach jedem Ausschalten eine neue an).

``merge_rides`` baut aus geparsten Fahrten eine künstliche ``Ride``: Samples der Reihe nach,
Streckenzähler und Kalorien fortlaufend. Die Pausen zwischen den Teilen bleiben als
Zeitsprung stehen; Auswertung und Export behandeln Sprünge über ``MAX_GAP_S`` bereits als
Pause (zählt weder als Fahrzeit noch als Strecke).
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime

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
