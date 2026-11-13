"""Tests für Ortsnamen, Routen-Vorschau und Namensregel – ohne Netz, nur erfundene Koordinaten."""

import io
import json
import urllib.error
from datetime import datetime

import pytest

from avinox.server import geocode, i18n
from avinox.server.geocode import GeocodeError, Nominatim, is_loop, ride_name, route_points, thumb_path


def track(coords):
    """GeoJSON mit Track aus (lat, lon)-Paaren."""
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"kind": "track"},
        "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in coords]},
    }]}


LINE = [(50.0, 10.0), (50.01, 10.0), (50.02, 10.01), (50.0, 10.001)]


def test_route_points_and_loop():
    start, end, far = route_points(track(LINE))
    assert start == (50.0, 10.0) and end == (50.0, 10.001) and far == (50.02, 10.01)
    assert is_loop(start, end)
    assert not is_loop((50.0, 10.0), (50.1, 10.0))
    assert route_points(track([(50.0, 10.0)])) is None
    assert route_points({"features": []}) is None


def test_thumb_path():
    d = thumb_path(track(LINE))
    assert d.startswith("M ") and d.count("L") == 3
    nums = [float(x) for x in d.replace("M", "").replace("L", "").split()]
    xs, ys = nums[0::2], nums[1::2]
    assert all(3 <= x <= 61 and 3 <= y <= 37 for x in xs for y in ys)
    # Norden oben: der nördlichste Punkt (Index 2) hat das kleinste y
    assert ys.index(min(ys)) == 2
    long = track([(50.0 + i * 0.001, 10.0 + i * 0.001) for i in range(200)])
    assert thumb_path(long, max_points=40).count("L") <= 40
    assert thumb_path({"features": []}) is None


NOW = datetime(2026, 5, 1, 9, 0)


def test_ride_name_places():
    full = {"start": "Aort", "end": "Bort", "far": "Cort"}
    assert ride_name(full, NOW, True, "de") == "Runde Aort – Cort"
    assert ride_name(full, NOW, True, "en") == "Loop Aort – Cort"
    assert ride_name({"start": "Aort", "end": "Aort", "far": "Aort"}, NOW, True, "de") == "Runde um Aort"
    assert ride_name({"start": "Aort", "end": None, "far": None}, NOW, True, "en") == "Loop around Aort"
    assert ride_name(full, NOW, False, "de") == "Aort → Bort"
    assert ride_name(full, NOW, False, "en") == "Aort → Bort"
    assert ride_name({"start": "Aort", "end": "Aort", "far": None}, NOW, False, "de") == "Runde um Aort"


@pytest.mark.parametrize("hour,de,en", [
    (6, "Morgenrunde", "Morning ride"), (12, "Mittagsrunde", "Lunch ride"), (15, "Nachmittagsrunde", "Afternoon ride"),
    (20, "Abendrunde", "Evening ride"), (23, "Nachtfahrt", "Night ride"), (3, "Nachtfahrt", "Night ride"),
])
def test_ride_name_daytime(hour, de, en):
    when = datetime(2026, 5, 1, hour, 30)
    assert ride_name(None, when, False, "de") == de
    assert ride_name({"start": None, "end": None, "far": None}, when, False, "en") == en


def test_ride_name_fallback():
    assert ride_name(None, None, False, "de") == "Fahrt"
    assert ride_name(None, None, False, "en") == "Ride"
    assert ride_name(None, None, False, "de", ride_id=7) == "Fahrt 7"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def nominatim(monkeypatch, payload=None, error=None):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["ua"] = req.full_url, req.get_header("User-agent")
        if error:
            raise error
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(geocode.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(geocode, "MIN_INTERVAL_S", 0)
    return Nominatim("https://geo.example/", "test-agent/1"), seen


def test_nominatim_picks_field(monkeypatch):
    geo, seen = nominatim(monkeypatch, {"address": {"county": "Kreis", "town": "Teststadt", "suburb": "Vorort"}})
    assert geo.reverse(50.0, 10.0) == "Teststadt"  # Reihenfolge: village, town, suburb, city, ...
    assert seen["url"].startswith("https://geo.example/reverse?format=jsonv2&lat=50.000&lon=10.000")
    assert "addressdetails=1" in seen["url"] and "zoom=14" in seen["url"]
    assert seen["ua"] == "test-agent/1"
    geo, _ = nominatim(monkeypatch, {"address": {"county": "Kreis"}})
    assert geo.reverse(50.0, 10.0) == "Kreis"
    geo, _ = nominatim(monkeypatch, {"address": {"country": "Land"}})
    assert geo.reverse(50.0, 10.0) is None
    geo, _ = nominatim(monkeypatch, {"error": "Unable to geocode"})
    assert geo.reverse(50.0, 10.0) is None


def test_nominatim_errors(monkeypatch):
    geo, _ = nominatim(monkeypatch, error=urllib.error.URLError("kein Netz"))
    with pytest.raises(GeocodeError):
        geo.reverse(50.0, 10.0)


class CountingGeo:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def reverse(self, lat, lon):
        self.calls += 1
        if self.fail:
            raise GeocodeError("down")
        return "Testdorf"


@pytest.fixture
def store(tmp_path):
    from avinox.server.config import load_settings
    from avinox.server.store import Store

    s = Store(load_settings(tmp_path / "data"))
    yield s
    s.close()


def add_rides(store):
    from test_server import ride_bytes

    for nr in (1000, 1001):
        store.add_file(f"ride_rec/4242/ride_rec_{nr}.proto", ride_bytes())


def meta(store):
    return store.db.query("SELECT path, places_json, geocoded_at FROM ride_meta ORDER BY path")


def test_geocode_pending_uses_cache(store):
    add_rides(store)
    geo = CountingGeo()
    assert store.geocode_pending(geo) == 2
    first_calls = geo.calls
    assert 1 <= first_calls <= 3  # zweite Fahrt am gleichen Ort: alles aus dem Cache
    assert all(r["geocoded_at"] for r in meta(store))
    assert store.geocode_pending(geo) == 0 and geo.calls == first_calls
    assert store.geocode_pending_count() == 0
    rides = store.list_rides()
    assert rides[0]["places"]["start"] == "Testdorf" and "Testdorf" in rides[0]["name"]
    assert rides[0]["thumb"] and rides[0]["is_loop"] in (True, False)


def test_geocode_error_aborts(store):
    add_rides(store)
    geo = CountingGeo(fail=True)
    assert store.geocode_pending(geo) == 0
    assert geo.calls == 1
    assert store.geocode_pending_count() == 2
    assert not [r for r in meta(store) if r["geocoded_at"]]


def test_set_ride_name_limits(store):
    add_rides(store)
    pk = store.list_rides()[0]["id"]
    assert store.set_ride_name(pk, "x" * 200)
    assert len(store.get_ride(pk)["name_custom"]) == 80
    assert store.set_ride_name(pk, "")
    assert store.get_ride(pk)["name_custom"] is None
    assert not store.set_ride_name(99999, "a")
