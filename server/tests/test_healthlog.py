"""HealthLog-Anbindung: HTTP wird ersetzt, nur Fake-Tokens und https://healthlog.example.invalid."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from avinox.ride_rec import parse_ride
from avinox.server import healthlog as hl
from avinox.server.app import create_app
from avinox.server.config import load_settings
from avinox.server.db import Database
from avinox.server.store import Store
from ridebuilder import ride
from test_track import T0, gps_sample

URL = "https://healthlog.example.invalid"
FUTURE = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
PAST = (datetime.now(UTC) - timedelta(hours=1)).isoformat()


def iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat()


def bundle(n, expires=FUTURE):
    return {"data": {"user": {"id": "u1", "username": "tester"}, "token": f"hlk_fake{n}", "tokenExpiresAt": expires, "refreshToken": f"hlr_fake{n}"}, "error": None, "meta": {}}


class FakeHL:
    """Ersetzt hl._request; protokolliert Aufrufe."""

    def __init__(self, routes=None):
        self.calls = []
        self.routes = routes or {}

    def __call__(self, method, url, headers, body, timeout=0):
        path = url.removeprefix(URL + "/api").split("?")[0]
        self.calls.append((method, path, url, headers, body))
        res = self.routes[(method, path)]
        res = res(body, headers) if callable(res) else res
        return res


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


@pytest.fixture
def fake(monkeypatch):
    f = FakeHL()
    monkeypatch.setattr(hl, "_request", f)
    return f


def connected(db, fake, expires=FUTURE):
    fake.routes[("POST", "/auth/login")] = (200, bundle(1, expires))
    hl.connect(db, URL, "tester", "pw-not-stored")


# ------------------------------------------------------------------ Anmeldung


def test_login_stores_tokens_not_password(db, fake):
    connected(db, fake)
    method, path, _, headers, body = fake.calls[0]
    assert headers["X-Client-Type"] == "native" and body == {"email": "tester", "password": "pw-not-stored"}
    st = hl.status(db)
    assert st == {"url": URL, "enabled": True, "connected": True, "username": "tester"}
    dump = json.dumps([dict(r) for r in db.query("SELECT * FROM settings")])
    assert "pw-not-stored" not in dump and "hlr_fake1" in dump


def test_login_rejected(db, fake):
    fake.routes[("POST", "/auth/login")] = (401, {"data": None, "error": "x", "meta": {}})
    with pytest.raises(hl.HealthLogError) as e:
        hl.connect(db, URL, "tester", "bad")
    assert e.value.code == "invalid_credentials" and not hl.is_connected(db)


def test_invalid_url(db):
    with pytest.raises(hl.HealthLogError) as e:
        hl.connect(db, "ftp://x", "u", "p")
    assert e.value.code == "invalid_url"
    assert hl.normalize_url(URL + "/api/") == URL


def test_mfa_path(db, fake):
    fake.routes[("POST", "/auth/login")] = (200, {"data": None, "error": None, "meta": {"mfaRequired": True, "mfaTicket": "ticket-fake", "methods": ["totp"]}})
    fake.routes[("POST", "/auth/mfa/verify")] = lambda body, h: (200, bundle(2)) if body["code"] == "123456" else (401, {})
    first = hl.connect(db, URL, "tester", "pw")
    assert first == {"mfa_required": True, "methods": ["totp"]} and not hl.is_connected(db)
    with pytest.raises(hl.HealthLogError) as e:
        hl.connect(db, URL, "tester", "pw", "000000")
    assert e.value.code == "mfa_invalid"
    assert hl.connect(db, URL, "tester", "pw", "123456")["mfa_required"] is False
    assert hl.is_connected(db)
    verify = [c for c in fake.calls if c[1] == "/auth/mfa/verify"][-1]
    assert verify[4] == {"mfaTicket": "ticket-fake", "method": "totp", "code": "123456"}


# ------------------------------------------------------------------ Refresh


def test_refresh_rotation_saved(db, fake):
    connected(db, fake, expires=PAST)
    fake.routes[("POST", "/auth/refresh")] = (200, bundle(2))
    fake.routes[("GET", "/workouts")] = (200, {"data": {"workouts": []}})
    fake.routes[("GET", "/measurements")] = (200, {"data": {"measurements": [], "meta": {}}})
    hl.fetch_heart_rate(db, datetime.fromtimestamp(T0, UTC), datetime.fromtimestamp(T0 + 60, UTC))
    refresh = [c for c in fake.calls if c[1] == "/auth/refresh"]
    assert len(refresh) == 1 and refresh[0][4] == {"refreshToken": "hlr_fake1"}
    assert db.get_setting("healthlog_refresh_token") == "hlr_fake2" and db.get_setting("healthlog_token") == "hlk_fake2"
    used = [c[3]["Authorization"] for c in fake.calls if c[1] == "/workouts"]
    assert used == ["Bearer hlk_fake2"]


def test_401_triggers_one_refresh_then_retry(db, fake):
    connected(db, fake)
    tokens = iter([401, 200])
    fake.routes[("GET", "/workouts")] = lambda b, h: (next(tokens), {"data": {"workouts": []}})
    fake.routes[("GET", "/measurements")] = (200, {"data": {"measurements": []}})
    fake.routes[("POST", "/auth/refresh")] = (200, bundle(2))
    hl.fetch_heart_rate(db, datetime.fromtimestamp(T0, UTC), datetime.fromtimestamp(T0 + 60, UTC))
    assert len([c for c in fake.calls if c[1] == "/auth/refresh"]) == 1


def test_refresh_401_disconnects(db, fake):
    connected(db, fake, expires=PAST)
    fake.routes[("POST", "/auth/refresh")] = (401, {"data": None, "meta": {"errorCode": "auth.refresh.reuse"}})
    with pytest.raises(hl.HealthLogError) as e:
        hl.fetch_heart_rate(db, datetime.fromtimestamp(T0, UTC), datetime.fromtimestamp(T0 + 60, UTC))
    assert e.value.code == "disconnected" and not hl.is_connected(db)
    assert db.get_setting("healthlog_refresh_token") is None


def test_disconnect_revokes(db, fake):
    connected(db, fake)
    fake.routes[("POST", "/auth/refresh")] = (200, {"data": {"revoked": True}})
    hl.disconnect(db)
    assert fake.calls[-1][4] == {"refreshToken": "hlr_fake1", "revoke": True}
    assert not hl.is_connected(db)


# ------------------------------------------------------------------ Abruf


def workout(wid, start, end, series=None):
    entry = {"id": wid, "startedAt": iso(start), "endedAt": iso(end)}
    detail = {"id": wid, "startedAt": iso(start), "hrSeries": {"source": "workout_series", "bucketSec": 10, "envelope": False, "points": [{"tSec": t, "mean": b, "min": b, "max": b} for t, b in (series or [])]}}
    return entry, detail


def setup_routes(fake, workouts, measurements=()):
    fake.routes[("GET", "/workouts")] = (200, {"data": {"workouts": [w[0] for w in workouts]}})
    for entry, detail in workouts:
        fake.routes[("GET", f"/workouts/{entry['id']}")] = (200, {"data": detail})
    fake.routes[("GET", "/measurements")] = (200, {"data": {"measurements": [{"measuredAt": iso(t), "value": v} for t, v in measurements], "meta": {}}})


def window(n=100):
    return datetime.fromtimestamp(T0, UTC), datetime.fromtimestamp(T0 + n, UTC)


def test_all_overlapping_workouts_used(db, fake):
    connected(db, fake)
    a = workout("w-a", T0 - 100, T0 + 40, [(0, 120), (100, 130), (140, 140)])
    b = workout("w-b", T0 + 60, T0 + 90, [(0, 150), (30, 160)])
    far = workout("w-far", T0 + 5000, T0 + 6000, [(0, 99)])
    setup_routes(fake, [a, far, b])
    res = hl.fetch_heart_rate(db, *window())
    assert res.workout_id == "w-a,w-b" and res.source == "healthlog_workout"
    assert [p[1] for p in res.points] == [120, 130, 140, 150, 160]
    assert res.points[0][0] == T0 - 100
    assert not any(c[1] == "/workouts/w-far" for c in fake.calls)


def test_measurements_fallback(db, fake):
    connected(db, fake)
    setup_routes(fake, [], [(T0 + 10, 100), (T0 + 70, 110)])
    res = hl.fetch_heart_rate(db, *window())
    assert res.source == "healthlog_measurements" and res.workout_id is None
    assert [p[:2] for p in res.points] == [(T0 + 10, 100), (T0 + 70, 110)]
    call = [c for c in fake.calls if c[1] == "/measurements"][0]
    assert "type=PULSE" in call[2]


def test_nothing_found(db, fake):
    connected(db, fake)
    setup_routes(fake, [])
    assert hl.fetch_heart_rate(db, *window()).points == []


def test_mixed_workout_wins_in_its_span(db, fake):
    connected(db, fake)
    w = workout("w1", T0, T0 + 100, [(0, 150), (50, 150), (100, 150)])
    setup_routes(fake, [w], [(T0 + 50, 90), (T0 + 400, 100), (T0 + 700, 110)])
    res = hl.fetch_heart_rate(db, *window(1000))
    assert res.source == "healthlog_mixed"
    by_t = {p[0]: p for p in res.points}
    assert by_t[T0 + 50][1:] == (150, "w")  # Workout gewinnt im eigenen Abschnitt
    assert by_t[T0 + 400][1:] == (100, "m") and by_t[T0 + 700][1:] == (110, "m")
    assert [p[0] for p in res.points] == sorted(by_t)


# ------------------------------------------------------------------ Zusammenführen


def hr_free_ride(n=100):
    samples = []
    for t in range(n):
        s = gps_sample(t, 5 * t)
        s.pop(51)
        samples.append(s)
    return ride(samples, start=T0, end=T0 + n)


def test_merge_interpolates_and_respects_gap():
    r = parse_ride(hr_free_ride())
    pts = [(T0 + 0, 100, "w"), (T0 + 10, 120, "w"), (T0 + 90, 140, "w"), (T0 + 99, 150, "w")]
    assert hl.merge_heart_rate(r, pts)
    hr = [s.heart_rate_bpm for s in r.samples]
    assert hr[0] == 100 and hr[5] == 110 and hr[10] == 120
    assert hr[50] == 130 and hr[90] == 140 and hr[99] == 150  # 80 s Lücke liegt unter MAX_GAP_S
    assert hl.interpolate(pts, T0 + 50, max_gap=60) is None  # größere Lücke bleibt ohne Wert


def test_merge_measurements_allow_bigger_gap():
    r = parse_ride(hr_free_ride())
    assert hl.merge_heart_rate(r, [(T0 + 0, 100, "m"), (T0 + 90, 118, "m")])
    assert r.samples[45].heart_rate_bpm == 109


def test_merge_belt_wins():
    r = parse_ride(ride([gps_sample(t, 5 * t) for t in range(10)], start=T0, end=T0 + 10))  # Feld 51 = 130
    assert not hl.merge_heart_rate(r, [(T0, 60, "w"), (T0 + 9, 60, "w")])
    assert {s.heart_rate_bpm for s in r.samples} == {130}


# ------------------------------------------------------------------ Store + Routen


@pytest.fixture
def store(tmp_path):
    s = Store(load_settings(tmp_path / "data"))
    yield s
    s.close()


def fake_result(points, source="healthlog_workout"):
    return hl.HrResult(points, source, "w1")


def test_store_merges_and_reprocesses(store):
    store.add_file("ride_rec/4242/ride_rec_1000.proto", hr_free_ride())
    assert store.hr_counts() == {"open": 1, "ok": 0}
    pts = [(T0 + t, 100 + t, "w") for t in range(0, 100, 10)] + [(T0 + 99, 199, "w")]
    now = datetime.fromtimestamp(T0 + 3600, UTC)
    assert store.healthlog_pending(fetch=lambda s, e: fake_result(pts), now=now) == 1
    assert store.hr_counts() == {"open": 0, "ok": 1}
    ride_pk = store.list_rides()[0]["id"]
    r = store.get_ride(ride_pk)
    assert r["analysis"]["hr_source"] == "healthlog_workout"
    summary = r["analysis"]["summary"]
    assert summary["avg_heart_rate_bpm"] and summary["max_heart_rate_bpm"] == 199
    assert r["analysis"]["distributions"]["heart_rate_s"] is not None
    assert "heart_rate_bpm" in store.ride_geojson(ride_pk)
    fit, _ = store.ride_fit(ride_pk)
    assert fit
    assert "<gpxtpx:hr>" in store.ride_gpx(ride_pk)[0]
    # Neuverarbeitung behält den Puls
    store.reprocess(only_outdated=False)
    assert store.get_ride(ride_pk)["analysis"]["hr_source"] == "healthlog_workout"


def test_belt_rides_never_queried(store):
    store.add_file("ride_rec/4242/ride_rec_1000.proto", ride([gps_sample(t, 5 * t) for t in range(10)], start=T0, end=T0 + 10))
    assert store.get_ride(store.list_rides()[0]["id"])["analysis"]["hr_source"] == "belt"
    calls = []
    store.healthlog_pending(fetch=lambda s, e: calls.append(1))
    assert calls == [] and store.hr_counts()["open"] == 0


def test_none_retry_rules(store):
    store.add_file("ride_rec/4242/ride_rec_1000.proto", hr_free_ride())
    calls = []

    def fetch(s, e):
        calls.append(1)
        return fake_result([])

    start = datetime.fromtimestamp(T0, UTC)
    store.healthlog_pending(fetch=fetch, now=start + timedelta(minutes=5))
    assert len(calls) == 1
    store.healthlog_pending(fetch=fetch, now=start + timedelta(minutes=30))  # zu früh
    assert len(calls) == 1
    store.healthlog_pending(fetch=fetch, now=start + timedelta(hours=2))  # jung genug, > 1 h
    assert len(calls) == 2
    store.healthlog_pending(fetch=fetch, now=start + timedelta(days=4))  # zu alt, nie wieder
    assert len(calls) == 2


def test_connection_error_pauses(store):
    store.add_file("ride_rec/4242/ride_rec_1000.proto", hr_free_ride())

    def boom(s, e):
        raise hl.HealthLogError("unreachable")

    assert store.healthlog_pending(fetch=boom) == 0
    assert store.db.one("SELECT 1 FROM ride_hr") is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    with TestClient(create_app(load_settings(tmp_path / "data"))) as c:
        yield c


def test_routes_never_return_tokens(client, fake):
    fake.routes[("POST", "/auth/login")] = (200, bundle(7))
    r = client.put("/api/v1/settings/healthlog", json={"url": URL})
    assert r.status_code == 200 and r.json()["connected"] is False
    r = client.post("/api/v1/settings/healthlog/connect", json={"username": "tester", "password": "pw-not-stored"})
    assert r.status_code == 200 and r.json()["connected"] and r.json()["mfa_required"] is False
    for resp in (r, client.get("/api/v1/settings/healthlog"), client.put("/api/v1/settings/healthlog", json={"enabled": True})):
        text = resp.text
        assert "hlk_" not in text and "hlr_" not in text and "pw-not-stored" not in text
    assert set(client.get("/api/v1/settings/healthlog").json()) == {"url", "enabled", "connected", "username", "open", "ok"}
    page = client.get("/settings")
    assert page.status_code == 200 and "hlk_" not in page.text
    fake.routes[("POST", "/auth/refresh")] = (200, {"data": {"revoked": True}})
    d = client.post("/api/v1/settings/healthlog/disconnect")
    assert d.json()["connected"] is False


def test_connect_route_mfa_and_errors(client, fake):
    fake.routes[("POST", "/auth/login")] = (200, {"data": None, "meta": {"mfaRequired": True, "mfaTicket": "t", "methods": ["totp"]}})
    body = {"url": URL, "username": "tester", "password": "pw"}
    r = client.post("/api/v1/settings/healthlog/connect", json=body)
    assert r.json()["mfa_required"] is True and r.json()["connected"] is False
    fake.routes[("POST", "/auth/login")] = (401, {})
    r = client.post("/api/v1/settings/healthlog/connect", json=body)
    assert r.status_code == 401 and r.json()["detail"]["error"] == "invalid_credentials"
    assert client.put("/api/v1/settings/healthlog", json={"url": "nope"}).status_code == 400


def test_retry_all_and_fetch_ride(store):
    store.add_file("ride_rec/4242/ride_rec_1000.proto", hr_free_ride())
    pk = store.list_rides()[0]["id"]
    path = store.db.one("SELECT path FROM rides WHERE id = ?", (pk,))["path"]
    store._save_hr(path, "none")
    store._save_hr("x/err", "error")
    store._save_hr("x/ok", "ok", "healthlog_workout", "w", [(1, 100, "w")])
    assert store.healthlog_retry_all() == 2
    assert [r["status"] for r in store.db.query("SELECT status FROM ride_hr")] == ["ok"]
    pts = [(T0 + t, 100 + t, "w") for t in range(0, 100, 10)]
    assert store.healthlog_fetch_ride(pk, fetch=lambda s, e: fake_result(pts)) == {"status": "ok", "source": "healthlog_workout"}
    # überschreibt vorhandenen ok-Eintrag
    pts2 = [(T0 + t, 150, "w") for t in range(0, 100, 10)]
    assert store.healthlog_fetch_ride(pk, fetch=lambda s, e: fake_result(pts2))["status"] == "ok"
    assert json.loads(store.db.one("SELECT points_json FROM ride_hr WHERE path = ?", (path,))["points_json"])[0][1] == 150
    assert store.healthlog_fetch_ride(pk, fetch=lambda s, e: fake_result([]))["status"] == "none"
    assert store.healthlog_fetch_ride(99999) is None


def test_fetch_ride_rejects_belt(store):
    store.add_file("ride_rec/4242/ride_rec_1000.proto", ride([gps_sample(t, 5 * t) for t in range(10)], start=T0, end=T0 + 10))
    pk = store.list_rides()[0]["id"]
    assert store.healthlog_fetch_ride(pk, fetch=lambda s, e: pytest.fail("no call"))["status"] == "belt"


def test_retry_and_ride_routes(client, fake, monkeypatch):
    assert client.post("/api/v1/settings/healthlog/retry").json()["retried"] == 0
    assert client.post("/api/v1/rides/999/healthlog").status_code == 404
    st = client.app.state.store
    st.add_file("ride_rec/4242/ride_rec_1000.proto", hr_free_ride())
    pk = st.list_rides()[0]["id"]
    assert client.post(f"/api/v1/rides/{pk}/healthlog").status_code == 409  # nicht verbunden
    fake.routes[("POST", "/auth/login")] = (200, bundle(9))
    client.put("/api/v1/settings/healthlog", json={"url": URL})
    client.post("/api/v1/settings/healthlog/connect", json={"username": "tester", "password": "pw"})
    pts = [(T0 + t, 100 + t, "w") for t in range(0, 100, 10)]
    monkeypatch.setattr(hl, "fetch_heart_rate", lambda db, s, e: fake_result(pts))
    r = client.post(f"/api/v1/rides/{pk}/healthlog")
    assert r.status_code == 200 and r.json()["status"] == "ok"

    def boom(db, s, e):
        raise hl.HealthLogError("unreachable")

    monkeypatch.setattr(hl, "fetch_heart_rate", boom)
    r = client.post(f"/api/v1/rides/{pk}/healthlog")
    assert r.status_code == 502 and r.json()["detail"]["error"] == "unreachable"
