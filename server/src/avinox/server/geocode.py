"""Ortsnamen für Fahrten: Reverse-Geocoding (Nominatim), Routen-Vorschau und Namensregel.

Die Hilfsfunktionen sind rein (ohne Netz) und einzeln testbar. Nur ``Nominatim.reverse``
spricht mit dem Dienst; Fehler werden als ``GeocodeError`` gemeldet, damit die Fahrt
später erneut versucht wird.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from . import i18n

LOOP_MAX_M = 1000
"""Start und Ziel höchstens so weit auseinander = Rundfahrt."""

MIN_INTERVAL_S = 1.1
"""Nominatim erlaubt höchstens eine Anfrage pro Sekunde."""

ADDRESS_KEYS = ("village", "town", "suburb", "city", "hamlet", "municipality", "county")


class GeocodeError(Exception):
    """Dienst nicht erreichbar oder unbrauchbare Antwort."""


class Nominatim:
    def __init__(self, base_url: str, user_agent: str, timeout: float = 10):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last = 0.0

    def reverse(self, lat: float, lon: float) -> str | None:
        query = urllib.parse.urlencode(
            {"format": "jsonv2", "lat": f"{lat:.3f}", "lon": f"{lon:.3f}", "zoom": 14, "addressdetails": 1, "accept-language": "de,en"}
        )
        req = urllib.request.Request(f"{self.base_url}/reverse?{query}", headers={"User-Agent": self.user_agent})
        with self._lock:  # Abstand zwischen zwei Anfragen einhalten, auch über Threads hinweg
            wait = self._last + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise GeocodeError(str(exc)) from exc
            finally:
                self._last = time.monotonic()
        address = data.get("address") if isinstance(data, dict) else None
        if not isinstance(address, dict):
            return None
        for key in ADDRESS_KEYS:
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


# ------------------------------------------------------------------ Geometrie


def _track_coords(geojson: dict) -> list[list[float]]:
    for feature in geojson.get("features", []):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        if props.get("kind") == "track" and geom.get("type") == "LineString":
            return geom.get("coordinates") or []
    return []


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def route_points(geojson: dict) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    """(Start, Ziel, entferntester Punkt) als (lat, lon); ``None`` bei weniger als 2 Punkten."""
    coords = _track_coords(geojson)
    if len(coords) < 2:
        return None
    pts = [(c[1], c[0]) for c in coords]
    start, end = pts[0], pts[-1]
    far = max(pts, key=lambda p: haversine_m(start, p))
    return start, end, far


def is_loop(start: tuple[float, float], end: tuple[float, float]) -> bool:
    return haversine_m(start, end) <= LOOP_MAX_M


def thumb_path(geojson: dict, w: float = 64, h: float = 40, pad: float = 3, max_points: int = 40) -> str | None:
    """SVG-Pfad (``d``) der Strecke in einer w×h-Box, Norden oben, Seitenverhältnis erhalten."""
    coords = _track_coords(geojson)
    if len(coords) < 2:
        return None
    step = max(1, math.ceil(len(coords) / max_points))
    picked = coords[::step]
    if picked[-1] is not coords[-1]:
        picked = [*picked, coords[-1]]
    lat0 = sum(c[1] for c in coords) / len(coords)
    k = math.cos(math.radians(lat0))
    xs = [c[0] * k for c in picked]
    ys = [-c[1] for c in picked]  # Norden = oben
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = max_x - min_x, max_y - min_y
    scale = min((w - 2 * pad) / span_x if span_x else math.inf, (h - 2 * pad) / span_y if span_y else math.inf)
    if not math.isfinite(scale):
        scale = 1.0
    off_x = (w - span_x * scale) / 2
    off_y = (h - span_y * scale) / 2
    parts = [f"{'M' if i == 0 else 'L'} {off_x + (x - min_x) * scale:.1f} {off_y + (y - min_y) * scale:.1f}" for i, (x, y) in enumerate(zip(xs, ys))]
    return " ".join(parts)


# ------------------------------------------------------------------ Namensregel


def ride_name(places: dict | None, start_local: datetime | None, loop: bool, lang: str, ride_id: int | None = None) -> str:
    start = (places or {}).get("start")
    if start:
        end = places.get("end")
        far = places.get("far")
        if loop:
            if far and far != start:
                return i18n.t("ride.name.loop_via", lang, a=start, b=far)
            return i18n.t("ride.name.loop", lang, a=start)
        if end and end != start:
            return i18n.t("ride.name.ab", lang, a=start, b=end)
        return i18n.t("ride.name.loop", lang, a=start)
    if start_local is not None:
        hour = start_local.hour
        if 5 <= hour < 11:
            key = "ride.name.morning"
        elif 11 <= hour < 14:
            key = "ride.name.noon"
        elif 14 <= hour < 18:
            key = "ride.name.afternoon"
        elif 18 <= hour < 22:
            key = "ride.name.evening"
        else:
            key = "ride.name.night"
        return i18n.t(key, lang)
    if ride_id is not None:
        return i18n.t("ride_n", lang, n=ride_id)
    return i18n.t("ride.name.plain", lang)
