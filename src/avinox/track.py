"""GPS-Strecke einer Fahrt: Punkte, Vereinfachung, GeoJSON und GPX.

GPS steht in den Samples (Feld 31/32) nur, wenn das Display einen Fix hatte.
Fahrten ohne GPS haben eventuell noch die 10 Vorschaupunkte im Header.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from .ride_rec import Ride

EARTH_RADIUS_M = 6_371_000
MAX_JUMP_KMH = 150
"""Sprünge, die schneller als das wären, gelten als GPS-Ausreißer."""
SIMPLIFY_TOLERANCE_M = 3.0


@dataclass(slots=True)
class TrackPoint:
    index: int
    """Index des Samples in der Fahrt."""
    lat: float
    lon: float
    altitude_m: float | None
    timestamp: int | None
    distance_m: int
    speed_kmh: float
    rider_power_w: float
    motor_power_w: float
    cadence_rpm: float
    heart_rate_bpm: int | None
    assist_level: int


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def track_points(ride: Ride) -> list[TrackPoint]:
    """GPS-Punkte der Fahrt, Ausreißer und doppelte Punkte entfernt."""
    out: list[TrackPoint] = []
    for i, s in enumerate(ride.samples):
        if s.latitude is None or s.longitude is None:
            continue
        if out:
            prev = out[-1]
            if (prev.lat, prev.lon) == (s.latitude, s.longitude):
                continue
            dist = haversine_m(prev.lat, prev.lon, s.latitude, s.longitude)
            dt = (s.timestamp - prev.timestamp) if s.timestamp is not None and prev.timestamp is not None else i - prev.index
            if dist / max(dt, 1) * 3.6 > MAX_JUMP_KMH:
                continue
        out.append(
            TrackPoint(
                index=i,
                lat=s.latitude,
                lon=s.longitude,
                altitude_m=s.altitude_m,
                timestamp=s.timestamp,
                distance_m=s.distance_m,
                speed_kmh=s.speed_kmh,
                rider_power_w=s.rider_power_w,
                motor_power_w=s.motor_power_w,
                cadence_rpm=s.cadence_rpm,
                heart_rate_bpm=s.heart_rate_bpm,
                assist_level=s.assist_level,
            )
        )
    return out


def track_length_m(points: list[TrackPoint]) -> float:
    return sum(haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(points, points[1:]))


def simplify(points: list[TrackPoint], tolerance_m: float = SIMPLIFY_TOLERANCE_M) -> list[TrackPoint]:
    """Douglas-Peucker in einer lokalen Meter-Projektion; Start und Ende bleiben."""
    if len(points) < 3:
        return list(points)
    lat0 = math.radians(sum(p.lat for p in points) / len(points))
    k = math.pi / 180 * EARTH_RADIUS_M
    xy = [(p.lon * k * math.cos(lat0), p.lat * k) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = xy[first], xy[last]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        worst, worst_i = -1.0, -1
        for i in range(first + 1, last):
            px, py = xy[i]
            if length == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / length
            if d > worst:
                worst, worst_i = d, i
        if worst > tolerance_m:
            keep[worst_i] = True
            stack.append((first, worst_i))
            stack.append((worst_i, last))
    return [p for p, k in zip(points, keep) if k]


def bounds(points: list[TrackPoint]) -> tuple[float, float, float, float] | None:
    """(min_lon, min_lat, max_lon, max_lat) wie GeoJSON-``bbox``."""
    if not points:
        return None
    return (min(p.lon for p in points), min(p.lat for p in points), max(p.lon for p in points), max(p.lat for p in points))


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def to_geojson(ride: Ride, points: list[TrackPoint] | None = None, tolerance_m: float = SIMPLIFY_TOLERANCE_M) -> dict:
    """GeoJSON-FeatureCollection: vereinfachte Linie mit Messwerten je Punkt, Start und Ziel.

    ``properties.samples`` hat je Koordinate die Werte für Einfärbung und Tooltip
    (Index, Distanz, Höhe, Tempo, Fahrer-/Motorleistung, Trittfrequenz, Puls, Stufe).
    Ohne GPS wird, falls vorhanden, die Vorschau aus dem Header geliefert.
    """
    points = track_points(ride) if points is None else points
    features: list[dict] = []
    if len(points) >= 2:
        line = simplify(points, tolerance_m)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[round(p.lon, 6), round(p.lat, 6)] + ([round(p.altitude_m, 1)] if p.altitude_m is not None else []) for p in line]},
                "properties": {
                    "kind": "track",
                    "ride_id": ride.header.ride_id,
                    "length_m": round(track_length_m(points)),
                    "points_total": len(points),
                    "fields": ["index", "distance_m", "altitude_m", "speed_kmh", "rider_power_w", "motor_power_w", "cadence_rpm", "heart_rate_bpm", "assist_level"],
                    "samples": [
                        [p.index, p.distance_m, _r(p.altitude_m, 1), round(p.speed_kmh, 1), round(p.rider_power_w), round(p.motor_power_w), round(p.cadence_rpm), p.heart_rate_bpm, p.assist_level]
                        for p in line
                    ],
                },
            }
        )
        for kind, p in (("start", points[0]), ("end", points[-1])):
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(p.lon, 6), round(p.lat, 6)]}, "properties": {"kind": kind}})
    elif len(ride.header.preview) >= 2:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[round(lon, 6), round(lat, 6)] for lat, lon in ride.header.preview]},
                "properties": {"kind": "preview", "ride_id": ride.header.ride_id},
            }
        )
    collection: dict = {"type": "FeatureCollection", "features": features}
    box = bounds(points) if points else None
    if box:
        collection["bbox"] = [round(v, 6) for v in box]
    return collection


def to_gpx(ride: Ride, points: list[TrackPoint] | None = None, name: str | None = None) -> str:
    """GPX 1.1 mit Höhe, Zeit (falls Uhrzeit bekannt), Puls, Trittfrequenz und Leistung.

    Puls/Trittfrequenz im Garmin-TrackPointExtension-Format, Fahrerleistung als
    ``<power>`` (wird von Strava & Co. gelesen).
    """
    points = track_points(ride) if points is None else points
    wallclock = ride.header.has_wallclock
    title = escape(name or f"Avinox Fahrt {ride.header.ride_id}")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="avinox-ridedata-server" xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2">',
    ]
    if wallclock and ride.header.start_time:
        lines.append(f"  <metadata><name>{title}</name><time>{ride.header.start_time.strftime('%Y-%m-%dT%H:%M:%SZ')}</time></metadata>")
    lines.append(f"  <trk><name>{title}</name><type>e-mountainbike</type><trkseg>")
    for p in points:
        parts = [f'    <trkpt lat="{p.lat:.7f}" lon="{p.lon:.7f}">']
        if p.altitude_m is not None:
            parts.append(f"<ele>{p.altitude_m:.1f}</ele>")
        if wallclock and p.timestamp:
            parts.append(f"<time>{datetime.fromtimestamp(p.timestamp, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}</time>")
        ext = []
        if p.heart_rate_bpm:
            ext.append(f"<gpxtpx:hr>{p.heart_rate_bpm}</gpxtpx:hr>")
        ext.append(f"<gpxtpx:cad>{round(p.cadence_rpm)}</gpxtpx:cad>")
        parts.append(f"<extensions><power>{round(p.rider_power_w)}</power><gpxtpx:TrackPointExtension>{''.join(ext)}</gpxtpx:TrackPointExtension></extensions>")
        parts.append("</trkpt>")
        lines.append("".join(parts))
    lines.append("  </trkseg></trk>")
    lines.append("</gpx>")
    return "\n".join(lines) + "\n"
