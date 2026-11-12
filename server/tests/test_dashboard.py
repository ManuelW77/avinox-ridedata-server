"""Tests für die Übersicht (Zeitreihen, Vergleich, Bestwerte) – nur synthetische Fahrten."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from avinox.server import dashboard as dash
from avinox.server.app import create_app
from avinox.server.config import load_settings
from avinox.server.store import Store
from ridebuilder import ride

TZ = ZoneInfo("Europe/Berlin")
TODAY = date(2026, 11, 12)  # Donnerstag, KW 46


def ride_at(local: datetime | None, seconds=600, km=5.0, rider_w=150, motor_w=300, battery=(80, 70), ride_id=1, ascent=50.0) -> bytes:
    start = int(local.replace(tzinfo=TZ).timestamp()) if local else 1000  # < Unix-Minimum: ohne Uhrzeit
    samples = []
    for t in range(seconds):
        bat = round(battery[0] - (battery[0] - battery[1]) * t / max(1, seconds - 1))
        samples.append({72: start + t, 7: 2000, 13: rider_w * 100, 14: motor_w * 100, 71: bat})
    return ride(samples, ride_id=ride_id, start=start, end=start + seconds, distance=int(km * 1000), ascent=ascent)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("AVINOX_UPLOAD_TOKEN", "AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD", "AVINOX_DATA_DIR", "AVINOX_TZ"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def store(tmp_path):
    s = Store(load_settings(tmp_path / "data"))
    yield s
    s.close()


def add(store, n, data):
    return store.add_file(f"ride_rec/1/ride_rec_{n}.proto", data)["ride"]


@pytest.fixture
def filled(store):
    ids = {
        # diese Woche (KW 46): zwei Fahrten
        "mon": add(store, 1, ride_at(datetime(2026, 11, 9, 10), km=10, rider_w=200, motor_w=200, ride_id=1)),
        "wed": add(store, 2, ride_at(datetime(2026, 11, 11, 17), km=6, rider_w=100, motor_w=300, ride_id=2, ascent=120)),
        # letzte Woche (KW 45)
        "last": add(store, 3, ride_at(datetime(2026, 11, 4, 9), km=8, rider_w=120, motor_w=400, battery=(90, 60), ride_id=3)),
        # Oktober, lange Fahrt mit wenig Motor
        "oct": add(store, 4, ride_at(datetime(2026, 10, 15, 8), seconds=1800, km=30, rider_w=250, motor_w=50, battery=(100, 90), ride_id=4)),
        # Testfahrt unter 1 km mit absurder Leistung – darf keinen Bestwert holen
        "test": add(store, 5, ride_at(datetime(2026, 11, 10, 12), seconds=60, km=0.3, rider_w=900, motor_w=900, ride_id=5)),
        # ohne Uhrzeit
        "undated": add(store, 6, ride_at(None, km=4, ride_id=6)),
    }
    return ids


def test_period_helpers():
    assert dash.period_start(TODAY, "week") == date(2026, 11, 9)
    assert dash.period_key(date(2026, 11, 9), "week") == "2026-W46"
    assert dash.shift(date(2026, 1, 1), "month", -1) == date(2025, 12, 1)
    assert dash.period_end(date(2026, 2, 1), "month") == date(2026, 2, 28)
    assert dash.steps_between(date(2026, 11, 1), date(2025, 11, 1), "month") == 12
    # ISO-Woche über den Jahreswechsel
    assert dash.period_key(dash.period_start(date(2027, 1, 1), "week"), "week") == "2026-W53"


def test_avg_power_time_weighted():
    acc = dash._empty()
    base = {"distance_m": 1000, "ascent_m": 0, "rider_kj": 0, "motor_kj": 0, "motor_wh": 0, "calories_kcal": 0, "battery_used_pct": 0}
    dash._add(acc, {**base, "avg_rider_power_w": 100, "moving_time_s": 3000})
    dash._add(acc, {**base, "avg_rider_power_w": 200, "moving_time_s": 1000})
    dash._add(acc, {**base, "avg_rider_power_w": None, "moving_time_s": 5000})
    assert dash._finish(acc)["avg_rider_power_w"] == 125


def test_empty_db(store):
    for period in dash.PERIODS:
        d = store.dashboard(period, today=TODAY)
        assert len(d["series"]) == dash.PERIODS[period]
        assert d["total"]["rides"] == 0 and d["highlights"] == [] and d["recent"] == []
        assert d["current"]["rides"] == 0 and d["max_offset"] == 0 and d["last_offset"] is None


def test_unknown_period(store):
    with pytest.raises(ValueError):
        store.dashboard("decade", today=TODAY)


def test_week_series_and_comparison(store, filled):
    d = store.dashboard("week", today=TODAY)
    assert len(d["series"]) == 26
    cur, prev = d["series"][-1], d["series"][-2]
    assert cur["key"] == "2026-W46" and cur["rides"] == 3  # inkl. Testfahrt
    assert prev["key"] == "2026-W45" and prev["rides"] == 1
    assert cur["distance_km"] == pytest.approx(16.3)
    assert d["current"]["name"] == "Diese Woche"
    c = d["comparison"]["distance_km"]
    assert c["previous"] == pytest.approx(8.0) and c["delta_pct"] == 104
    assert "ggü. Vorwoche" in d["headline"]
    # Energie und energiegewichteter Motoranteil
    assert cur["rider_kj"] == pytest.approx(0.6 * 200 + 0.6 * 100 + 0.06 * 900, abs=0.5)
    motor_kj = cur["motor_kj"]
    assert cur["motor_share_pct"] == pytest.approx(100 * motor_kj / (motor_kj + cur["rider_kj"]), abs=0.1)
    assert prev["battery_used_pct"] == 30
    # Fahrten ohne Uhrzeit fehlen in der Zeitreihe, zählen aber gesamt
    assert sum(s["rides"] for s in d["series"]) == 5
    assert d["total"]["rides"] == 6 and d["total"]["undated_rides"] == 1


def test_offset_and_other_periods(store, filled):
    d = store.dashboard("week", offset=1, today=TODAY)
    assert d["current"]["key"] == "2026-W45" and d["current"]["name"] == "Letzte Woche"
    assert d["series"][-1]["key"] == "2026-W45"
    # Offset wird auf den Zeitraum der ersten Fahrt begrenzt
    far = store.dashboard("week", offset=999, today=TODAY)
    assert far["offset"] == far["max_offset"] == 4
    assert far["current"]["key"] == "2026-W42"

    m = store.dashboard("month", today=TODAY)
    assert len(m["series"]) == 24
    assert m["series"][-1]["key"] == "2026-11" and m["series"][-1]["rides"] == 4
    assert m["series"][-2]["key"] == "2026-10" and m["series"][-2]["distance_km"] == pytest.approx(30)
    y = store.dashboard("year", today=TODAY)
    assert [s["key"] for s in y["series"]] == ["2022", "2023", "2024", "2025", "2026"]
    day = store.dashboard("day", today=TODAY)
    assert len(day["series"]) == 30 and day["series"][-2]["key"] == "2026-11-11"
    assert day["current"]["rides"] == 0 and day["last_offset"] == 1


def test_highlights(store, filled):
    d = store.dashboard("week", today=TODAY)
    h = {x["key"]: x for x in d["highlights"]}
    assert filled["test"] not in {x.get("ride") for x in d["highlights"]}  # Testfahrt ignoriert
    assert h["rider_kj"]["ride"] == filled["oct"]
    assert h["rider_kj"]["sentence"] == "Am Do 15.10.2026 hattest du die meiste Eigenleistung: 450 kJ."
    assert h["distance"]["ride"] == filled["oct"]
    assert h["motor_share_max"]["ride"] == filled["last"]
    assert h["motor_share_min"]["ride"] == filled["oct"]
    assert h["ascent"]["ride"] == filled["wed"]
    assert h["km_per_pct"]["ride"] == filled["oct"]  # 30 km / 10 %
    for x in d["highlights"]:
        assert x["value_text"] in x["sentence"]
    # Kein Zeitraum übertrifft die Oktober-Einzelfahrt – also keine doppelten Zeitraum-Bestwerte
    assert not [x for x in d["highlights"] if x["group"] == "period"]


def test_period_highlights(store):
    add(store, 1, ride_at(datetime(2026, 11, 9, 8), km=10, ride_id=1))
    add(store, 2, ride_at(datetime(2026, 11, 9, 16), km=12, ride_id=2))
    add(store, 3, ride_at(datetime(2026, 11, 11, 16), km=5, ride_id=3))
    h = {x["key"]: x for x in store.dashboard("week", today=TODAY)["highlights"]}
    assert h["day_distance_km"]["sentence"] == "Am Mo 09.11.2026 hast du an einem Tag die längste Strecke geschafft: 22,0 km bei 2 Fahrten."
    assert h["day_distance_km"]["offset"] == 3
    assert h["week_distance_km"]["value"] == pytest.approx(27) and h["week_distance_km"]["offset"] == 0
    assert "month_distance_km" not in h  # gleich wie die Woche


def test_undated_sentence(store):
    add(store, 1, ride_at(None, km=4, ride_id=77))
    h = {x["key"]: x for x in store.dashboard("week", today=TODAY)["highlights"]}
    assert h["distance"]["sentence"].startswith("Bei Fahrt 77 (ohne Datum)")
    assert h["distance"]["date"] is None


def test_emphasize_escapes():
    html = dash.emphasize("A <b> 5 kJ.", "5 kJ")
    assert str(html) == "A &lt;b&gt; <strong>5 kJ</strong>."


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(load_settings(tmp_path / "data"))) as c:
        yield c


def test_api_and_pages(client):
    assert client.get("/api/v1/dashboard").status_code == 200
    assert client.get("/").status_code == 200 and "Noch keine Fahrten" in client.get("/").text
    store = client.app.state.store
    add(store, 1, ride_at(datetime(2026, 11, 9, 10), km=10, ride_id=1))
    r = client.get("/api/v1/dashboard", params={"period": "month", "offset": 0})
    assert r.status_code == 200 and r.json()["period"] == "month"
    assert client.get("/api/v1/dashboard", params={"period": "decade"}).status_code == 422
    assert client.get("/api/v1/dashboard", params={"offset": -1}).status_code == 422
    page = client.get("/", params={"period": "year"})
    assert page.status_code == 200 and "Bestwerte" in page.text and "dashboard.js" in page.text
    assert "<strong>" in page.text
    assert client.get("/", params={"period": "unsinn", "offset": "5"}).status_code == 200
    rides = client.get("/rides")
    assert rides.status_code == 200 and "/rides/" in rides.text
    assert client.get("/static/dashboard.js").status_code == 200
