"""Fahrten zusammenführen und wieder trennen – nur synthetische Daten."""

import pytest
from fastapi.testclient import TestClient

from avinox.merge import suggest_pairs
from avinox.server import store as store_mod
from avinox.server.app import create_app
from ridebuilder import ride
from test_server import auth, clean_env, put, settings  # noqa: F401
from test_track import T0, gps_sample


def ride_at(offset_s: int, n: int = 40, ascent: float = 10.0, alt0: float = 500.0) -> bytes:
    """Fahrt, die ``offset_s`` Sekunden nach T0 beginnt; 5 m pro Sekunde, Anstieg über die Höhe."""
    samples = [gps_sample(offset_s + t, 5 * t, alt=alt0 + ascent * t / n) for t in range(n)]
    return ride(samples, ride_id=offset_s, start=T0 + offset_s, end=T0 + offset_s + n, distance=5 * n, ascent=ascent, descent=2.0)


def path(i: int) -> str:
    return f"ride_rec/9/ride_rec_{1000 + i}.proto"


@pytest.fixture
def client(settings):  # noqa: F811
    with TestClient(create_app(settings)) as c:
        yield c


def upload(client, settings, offsets):
    return [put(client, settings, ride_at(off), path=path(i)).json()["ride"] for i, off in enumerate(offsets)]


def test_merge_sums_and_list(client, settings):
    a, b, c = upload(client, settings, [0, 600, 100_000])
    parts = {r["id"]: r for r in client.get("/api/v1/rides").json()}
    r = client.post("/api/v1/rides/merge", json={"ids": [a, b]})
    assert r.status_code == 200
    mid = r.json()["id"]
    rides = client.get("/api/v1/rides").json()
    assert sorted(x["id"] for x in rides) == sorted([mid, c])
    m = next(x for x in rides if x["id"] == mid)
    assert m["merged_count"] == 2
    assert m["distance_m"] == parts[a]["distance_m"] + parts[b]["distance_m"]
    # je Übergang fehlt höchstens die letzte Sekunde des Teils davor (Zeitsprung zählt 0)
    assert abs(m["moving_time_s"] - (parts[a]["moving_time_s"] + parts[b]["moving_time_s"])) <= 1
    assert m["ascent_m"] == pytest.approx(parts[a]["ascent_m"] + parts[b]["ascent_m"])
    assert m["duration_s"] == parts[a]["duration_s"] + parts[b]["duration_s"]
    assert client.get(f"/api/v1/rides/{a}").status_code == 404
    full = client.get(f"/api/v1/rides/{mid}").json()
    assert full["merged_count"] == 2
    # Pause (560 s) zählt nicht als Fahrzeit; Anzeige-Dauer bleibt Summe der Teile
    assert full["analysis"]["summary"]["elapsed_s"] == 640
    # Ausgangsfahrten sind als Teil bekannt: erneuter Upload derselben Datei ändert nichts
    assert put(client, settings, ride_at(0), path=path(0)).json()["new"] is False
    assert len(client.get("/api/v1/rides").json()) == 2


def test_merge_pages_and_exports(client, settings):
    a, b = upload(client, settings, [0, 600])
    mid = client.post("/api/v1/rides/merge", json={"ids": [a, b]}).json()["id"]
    assert "2" in client.get("/rides").text
    page = client.get(f"/rides/{mid}")
    assert page.status_code == 200 and 'id="split-btn"' in page.text
    assert client.get(f"/rides/{mid}", headers={"Accept-Language": "en"}).status_code == 200
    gpx = client.get(f"/api/v1/rides/{mid}/gpx")
    assert gpx.status_code == 200 and gpx.text.count("<trkpt") >= 70
    assert client.get(f"/api/v1/rides/{mid}/fit").status_code == 200
    assert client.get(f"/api/v1/rides/{mid}/geojson").status_code == 200
    assert client.get(f"/api/v1/rides/{mid}/raw").status_code == 404
    store = client.app.state.store
    store.set_physics_config(90, None, None)
    assert store.ride_physics(mid) is not None
    dash = client.get("/api/v1/dashboard").json() if client.get("/api/v1/dashboard").status_code == 200 else None
    if dash is not None:
        assert "error" not in dash


def test_dashboard_counts_once(client, settings):
    a, b = upload(client, settings, [0, 600])
    store = client.app.state.store
    before = store.stats()["total"]
    client.post("/api/v1/rides/merge", json={"ids": [a, b]})
    after = store.stats()["total"]
    assert after["rides"] == 1
    assert after["distance_km"] == before["distance_km"]
    assert abs(after["moving_time_s"] - before["moving_time_s"]) <= 1
    dash = store.dashboard("all")
    assert dash is not None


def test_split_restores_and_survives_reprocess(client, settings):
    ids = upload(client, settings, [0, 600])
    before = {(r["path"], r["distance_m"], r["moving_time_s"]) for r in client.get("/api/v1/rides").json()}
    mid = client.post("/api/v1/rides/merge", json={"ids": ids}).json()["id"]
    store = client.app.state.store
    store.reprocess(only_outdated=False)
    rides = client.get("/api/v1/rides").json()
    assert len(rides) == 1 and rides[0]["merged_count"] == 2
    new_id = rides[0]["id"]
    assert client.post(f"/api/v1/rides/{new_id}/split").json()["ok"] is True
    after = {(r["path"], r["distance_m"], r["moving_time_s"]) for r in client.get("/api/v1/rides").json()}
    assert after == before
    assert client.post(f"/api/v1/rides/{mid}/split").status_code == 404
    assert store.db.query("SELECT * FROM ride_merges") == []
    assert store.db.one("SELECT 1 FROM rides WHERE path LIKE 'merge:%'") is None


def test_delete_merged_removes_all_parts(client, settings):
    ids = upload(client, settings, [0, 600])
    mid = client.post("/api/v1/rides/merge", json={"ids": ids}).json()["id"]
    assert client.delete(f"/api/v1/rides/{mid}").status_code == 200
    assert client.get("/api/v1/rides").json() == []
    store = client.app.state.store
    store.reprocess(only_outdated=False)
    assert store.list_rides() == []
    # neuer Upload eines Teils bleibt gesperrt
    assert put(client, settings, ride_at(0), path=path(0)).json()["ride"] is None


def test_merge_name_and_regroup(client, settings):
    a, b, c = upload(client, settings, [0, 600, 1200])
    client.put(f"/api/v1/rides/{a}/name", json={"name": "Feierabendrunde"})
    mid = client.post("/api/v1/rides/merge", json={"ids": [a, b]}).json()["id"]
    assert client.get(f"/api/v1/rides/{mid}").json()["name"] == "Feierabendrunde"
    # Gruppe plus weitere Fahrt: zu einer Fahrt aus drei Teilen
    mid2 = client.post("/api/v1/rides/merge", json={"ids": [mid, c]}).json()["id"]
    rides = client.get("/api/v1/rides").json()
    assert len(rides) == 1 and rides[0]["merged_count"] == 3 and rides[0]["id"] == mid2
    assert client.post(f"/api/v1/rides/{mid2}/split").json()["ids"].__len__() == 3
    # Namen der Einzelfahrt bleibt erhalten
    assert any(r["name"] == "Feierabendrunde" for r in client.get("/api/v1/rides").json())


def test_merge_geocode_pending(client, settings):
    ids = upload(client, settings, [0, 600])
    client.post("/api/v1/rides/merge", json={"ids": ids})
    store = client.app.state.store
    assert store.geocode_pending_count() == 1


def test_merge_errors(client, settings):
    a, = upload(client, settings, [0])
    assert client.post("/api/v1/rides/merge", json={"ids": [a]}).status_code == 400
    assert client.post("/api/v1/rides/merge", json={"ids": [a, 9999]}).status_code == 404
    assert client.post("/api/v1/rides/merge", json={"ids": "x"}).status_code == 400
    # ohne Uhrzeit (Sekunden seit Gerätestart)
    nt = ride([gps_sample(t, 5 * t) | {72: 100 + t} for t in range(10)], start=100, end=110, distance=50)
    n = put(client, settings, nt, path=path(5)).json()["ride"]
    r = client.post("/api/v1/rides/merge", json={"ids": [a, n]})
    assert r.status_code == 409


def test_merge_requires_auth(settings):
    settings.web_user, settings.web_password = "fahrer", "geheim"
    with TestClient(create_app(settings)) as c:
        assert c.post("/api/v1/rides/merge", json={"ids": [1, 2]}).status_code == 401
        assert c.post("/api/v1/rides/1/split").status_code == 401


def test_suggestions():
    def item(i, start, elapsed=600):
        return {"id": i, "start": start, "elapsed_s": elapsed}

    base = "2026-11-01T10:00:00+00:00"
    items = [
        item(1, base),                              # endet 10:10
        item(2, "2026-11-01T10:40:00+00:00"),       # 30 min Pause: ja
        item(3, "2026-11-01T12:30:00+00:00"),       # 100 min Pause: nein
        item(4, None),                              # ohne Start: nie
        item(5, "2026-11-01T12:45:00+00:00"),       # 5 min Pause: ja
    ]
    assert suggest_pairs(items) == {2: 1, 5: 3}
    assert suggest_pairs([item(1, base), item(2, "2026-11-01T11:10:00+00:00")]) == {}  # genau 60 min: nein


def test_list_shows_hint(client, settings):
    a, b, c = upload(client, settings, [0, 600, 100_000])
    rides = {r["id"]: r for r in client.get("/api/v1/rides").json()}
    assert rides[b]["merge_prev"] == a and rides[a]["merge_prev"] is None and rides[c]["merge_prev"] is None
    html = client.get("/rides").text
    assert f'data-merge="{a},{b}"' in html
    assert html.count("data-merge=") == 1
