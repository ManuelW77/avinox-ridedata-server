"""Fahrphysik: Berechnung aus dem Modell, Zwischenspeicher, Einstellungen, API und MCP. Nur synthetische Daten."""

from __future__ import annotations

import json
import math
import random

import pytest
from fastapi.testclient import TestClient

from avinox import physics
from avinox.ride_rec import Sample
from avinox.server import ai
from avinox.server.app import create_app
from avinox.server.config import load_settings
from ridebuilder import ride
from test_mcp import call, enable, rpc

G = 9.81
LAT0, LON0 = 47.0, 8.0
T0 = 1_800_000_000
TRUE_CRR, TRUE_CDA, MASS, ETA = 0.017, 0.65, 132.0, 0.96


def _rho(temp_c=15.0, kpa=101.3):
    return kpa * 1000 / (287.05 * (temp_c + 273.15))


def hill(x: float) -> float:
    """Höhenprofil entlang der Strecke: flach bis leicht wellig (maximal rund 3 % Steigung)."""
    return 6 * math.sin(x / 450) + 3 * math.sin(x / 160)


def varying_speed(t: float) -> float:
    """m/s: 13,5 bis 28,5 km/h in flachen Rampen (Beschleunigung unter 0,15 m/s²)."""
    kmh = 21 + 7.5 * math.tanh(1.5 * math.sin(2 * math.pi * t / 170)) / math.tanh(1.5)
    return kmh / 3.6


def constant_speed(t: float) -> float:
    return 22.0 / 3.6


def make_samples(n=3000, speed=varying_speed, crr=TRUE_CRR, cda=TRUE_CDA, mass=MASS, wind=0.0, out_and_back=False,
                 dips=(), seed=1, battery=(90, 60), assist=2, split=0.5, temp=15.0, kpa=101.3) -> list[Sample]:
    """Fahrt aus dem Modell: P = (Crr·m·g·cos α + ½·ρ·CdA·v_luft² + m·g·sin α + m·a)·v / η.

    ``wind`` > 0: Gegenwind auf dem Hinweg, Rückenwind zurück (nur mit ``out_and_back``).
    ``dips``: Startsekunden von Bremsabschnitten (Tempo fällt in 8 s auf 60 %, Leistung 0).
    """
    rng = random.Random(seed)
    rho = _rho(temp, kpa)
    x, direction, prev_v = 0.0, 1, speed(0)
    dist = 0.0
    out = []
    for t in range(n):
        v = speed(t)
        for d in dips:
            if d <= t < d + 16:
                v *= 1 - 0.4 * (1 - abs((t - d) - 8) / 8)
        if out_and_back and t == int(n * split):
            direction = -1
        a = v - prev_v if t else 0.0
        prev_v = v
        slope = (hill(x + 1) - hill(x - 1)) / 2 * direction
        alpha = math.atan(slope)
        v_air = v + (wind if direction == 1 else -wind) if out_and_back else v
        force = crr * mass * G * math.cos(alpha) + 0.5 * rho * cda * v_air * abs(v_air) + mass * G * math.sin(alpha) + mass * a
        braking = any(d <= t < d + 16 for d in dips)
        power = 0.0 if braking else max(force * v / ETA, 0.0) + rng.gauss(0, 2)
        power = max(power, 0.0)
        rider = power / (1 + 1.5)  # Faktor 1,5 Motor je Fahrer-Watt
        x += v * direction
        dist += v
        pct = round(battery[0] + (battery[1] - battery[0]) * t / max(n - 1, 1))
        out.append(Sample(
            seq=t, timestamp=T0 + t, speed_kmh=v * 3.6 + rng.gauss(0, 0.02), assist_level=assist, cadence_rpm=80.0,
            motor_torque_nm=0.0, rider_torque_nm=0.0, total_torque_nm=0.0, rider_power_w=rider, motor_power_w=power - rider,
            total_power_w=power, gear=5, distance_m=int(dist), latitude=LAT0 + x / 111_195, longitude=LON0,
            imu=(None, None, None), altitude_m=500 + hill(x) + rng.gauss(0, 0.05), gradient_pct=None, temperature_c=temp,
            pressure_kpa=kpa, heart_rate_bpm=None, calories_kcal=None, battery_pct=pct, odometer_m=None, event=0,
        ))
    return out


# ------------------------------------------------------------------ Widerstand


def test_fit_recovers_crr_and_cda():
    out = physics.analyze_physics(make_samples(), MASS)
    res = out["resistance"]
    assert res["cda_source"] == "fitted"
    assert res["crr"] == pytest.approx(TRUE_CRR, abs=0.0015)
    assert res["cda"] == pytest.approx(TRUE_CDA, abs=0.1)
    assert res["crr_p10"] <= res["crr"] <= res["crr_p90"]
    assert res["rho_source"] == "measured" and res["rho"] == pytest.approx(_rho(), abs=0.001)
    assert res["seconds_used"] > 1500 and "cda_assumed" not in out["warnings"]
    assert out["is_estimate"] is True and "crr" in res["estimated_fields"]


def test_flat_power_matches_model():
    res = physics.analyze_physics(make_samples(), MASS)["resistance"]
    v = 25 / 3.6
    expected = (TRUE_CRR * MASS * G * v + 0.5 * _rho() * TRUE_CDA * v ** 3) / ETA
    flat = res["flat_power_w"]["25"]
    assert flat["w"] == pytest.approx(expected, rel=0.06)
    assert abs(flat["w"] - (flat["roll_w"] + flat["air_w"])) <= 1
    assert res["flat_power_w"]["20"]["w"] < flat["w"]


def test_constant_speed_assumes_cda_but_crr_is_right():
    out = physics.analyze_physics(make_samples(speed=constant_speed), MASS, cda=TRUE_CDA)
    res = out["resistance"]
    assert res["cda_source"] == "assumed" and res["cda"] == TRUE_CDA
    assert res["crr"] == pytest.approx(TRUE_CRR, abs=0.0015)
    assert "cda_assumed" in out["warnings"]


def test_default_cda_is_used_when_not_given():
    res = physics.analyze_physics(make_samples(speed=constant_speed), MASS)["resistance"]
    assert res["cda"] == physics.DEFAULT_CDA


def test_braking_sections_are_filtered():
    clean = physics.analyze_physics(make_samples(), MASS)["resistance"]
    dips = tuple(range(100, 2900, 280))
    dirty = physics.analyze_physics(make_samples(dips=dips), MASS)["resistance"]
    assert dirty["seconds_used"] < clean["seconds_used"]
    assert dirty["crr"] == pytest.approx(TRUE_CRR, abs=0.0015)


def test_too_little_data_gives_null_with_reason():
    out = physics.analyze_physics(make_samples(n=100), MASS)
    assert out["resistance"] is None and out["resistance_reason"] == "too_little_valid_data"
    assert "few_samples" in out["warnings"]
    empty = physics.analyze_physics([], MASS)
    assert empty["resistance"] is None and empty["resistance_reason"] == "no_samples"


def test_gaps_split_sections():
    samples = make_samples(n=600)
    for i, s in enumerate(samples):
        if i >= 300:
            s.timestamp += 60  # Pause von einer Minute
    prep = physics.prepare(samples)
    assert len(prep.sec) == 2 and prep.sec[0] == (0, 300)
    assert prep.w[299] == 0


def test_air_density_falls_back_to_default():
    samples = make_samples(n=400)
    for s in samples:
        s.pressure_kpa = 20.0  # unplausibel
    out = physics.analyze_physics(samples, MASS)
    assert out["resistance"]["rho"] == physics.RHO_DEFAULT and out["resistance"]["rho_source"] == "default"
    assert "rho_default" in out["warnings"]


def test_device_temperature_is_flagged():
    samples = make_samples(n=1200)
    for i, s in enumerate(samples):
        s.temperature_c = 18.0 - 8.0 * min(i / 600, 1)  # warmer Start, kühlt auf Umgebung ab
    out = physics.analyze_physics(samples, MASS)
    assert "temp_is_device" in out["warnings"] and out["conditions"]["temp_note"] == "device_temperature"
    assert out["conditions"]["min_temp_c"] == 10.0 and out["conditions"]["max_temp_c"] == 18.0


# ------------------------------------------------------------------ Hin- und Rückweg


def test_out_and_back_cancels_wind():
    samples = make_samples(n=3000, out_and_back=True, wind=2.0)
    cond = physics.analyze_physics(samples, MASS)["conditions"]["out_and_back"]
    assert cond["detected"] is True
    assert cond["crr_mean"] == pytest.approx(TRUE_CRR, abs=0.003)
    assert abs(cond["crr_out"] - TRUE_CRR) > 0.004 and abs(cond["crr_back"] - TRUE_CRR) > 0.004
    assert cond["crr_out"] > TRUE_CRR > cond["crr_back"]  # Gegenwind hin, Rückenwind zurück
    assert not {"lat", "lon", "latitude", "longitude"} & set(cond)


def test_one_way_ride_is_not_out_and_back():
    cond = physics.analyze_physics(make_samples(), MASS)["conditions"]["out_and_back"]
    assert cond == {"detected": False}


# ------------------------------------------------------------------ Energie und Akku


def test_energy_block_and_physics_ratio():
    out = physics.analyze_physics(make_samples(), MASS, battery_wh=800)
    en = out["energy"]
    samples = make_samples()
    rider_wh = sum(s.rider_power_w for s in samples[:-1]) / 3600
    motor_wh = sum(s.motor_power_w for s in samples[:-1]) / 3600
    km = (samples[-1].distance_m - samples[0].distance_m) / 1000
    assert en["wh_per_km"] == pytest.approx((rider_wh + motor_wh) / km, rel=0.01)
    assert en["motor_share_pct"] == pytest.approx(100 * motor_wh / (rider_wh + motor_wh), abs=0.5)
    assert en["physics_ratio"] == pytest.approx(1.0, abs=0.1)


def test_battery_efficiency():
    out = physics.analyze_physics(make_samples(battery=(90, 60)), MASS, battery_wh=800)
    batt = out["energy"]["battery"]
    assert batt["used_pct"] == 30 and batt["battery_wh"] == 240.0
    assert batt["motor_battery_efficiency_pct"] == pytest.approx(100 * batt["motor_wh"] / 240, abs=0.1)
    assert 40 < batt["motor_battery_efficiency_pct"] < 150


def test_battery_missing_capacity_or_data_is_null_with_reason():
    en = physics.analyze_physics(make_samples(), MASS)["energy"]
    assert en["battery"] is None and en["battery_reason"] == "battery_capacity_missing"
    en = physics.analyze_physics(make_samples(battery=(90, 88)), MASS, battery_wh=800)["energy"]
    assert en["battery_reason"] == "battery_used_too_small"
    samples = make_samples()
    for s in samples:
        s.battery_pct = None
    assert physics.analyze_physics(samples, MASS, battery_wh=800)["energy"]["battery_reason"] == "no_battery_data"


# ------------------------------------------------------------------ Bergauf


def climb_samples(assist=2, rider=80.0, motor=160.0) -> list[Sample]:
    """200 s flach, dann 300 s mit 8 % bei 12 km/h; Fahrer- und Motorleistung fest."""
    out, x, alt = [], 0.0, 500.0
    for t in range(500):
        climbing = t >= 200
        v = 12 / 3.6 if climbing else 20 / 3.6
        x += v
        alt += v * 0.08 if climbing else 0
        r, m = (rider, motor) if climbing else (100.0, 0.0)
        out.append(Sample(
            seq=t, timestamp=T0 + t, speed_kmh=v * 3.6, assist_level=assist if climbing else 0, cadence_rpm=70.0,
            motor_torque_nm=0.0, rider_torque_nm=0.0, total_torque_nm=0.0, rider_power_w=r, motor_power_w=m, total_power_w=r + m,
            gear=3, distance_m=int(x), latitude=None, longitude=None, imu=(None, None, None), altitude_m=alt, gradient_pct=None,
            temperature_c=None, pressure_kpa=None, heart_rate_bpm=None, calories_kcal=None, battery_pct=None, odometer_m=None, event=0,
        ))
    return out


def test_climbing_values_and_assist_factor():
    out = physics.analyze_physics(climb_samples(), MASS)
    c = out["climbing"]
    assert c["segments"] == 1 and 270 <= c["seconds"] <= 300
    assert c["total_w_per_kg"] == pytest.approx(240 / MASS, abs=0.02)
    assert c["rider_w_per_kg"] == pytest.approx(80 / MASS, abs=0.02)
    assert c["vam_m_per_h"] == pytest.approx(0.08 * (12 / 3.6) * 3600, rel=0.1)
    assert c["mean_grade_pct"] == pytest.approx(8.0, abs=0.5)
    level = next(a for a in c["assist"] if a["level"] == 2)  # Fenster beginnt kurz vor dem Anstieg (Stufe aus)
    assert level["name"] == "trail" and level["factor"] == 2.0 and level["seconds_pedaling"] == level["seconds"] >= 250


def test_assist_factor_ignores_low_rider_power_and_needs_seconds():
    samples = climb_samples(rider=20.0, motor=160.0)
    out = physics.analyze_physics(samples, MASS)["climbing"]
    level = next(a for a in out["assist"] if a["level"] == 2)
    assert level["factor"] is None and level["seconds_pedaling"] == 0 and level["seconds"] >= 250
    factors = physics.assist_factors(climb_samples(), [1] * 500)
    assert {f["level"] for f in factors} == {0, 2}
    assert next(f for f in factors if f["level"] == 0)["factor"] == 0.0


def test_no_climb_is_null_with_reason():
    out = physics.analyze_physics(make_samples(), MASS)
    assert out["climbing"] is None and out["climbing_reason"] == "no_climb"


# ------------------------------------------------------------------ Vergleich (reine Funktionen)


def test_outlier_flag_needs_three_rides_and_uses_median():
    items = [{"crr": c} for c in (0.015, 0.016, 0.017, 0.030)]
    flags = [i["outlier"] for i in physics.mark_outliers(items)]
    assert flags == [False, False, False, True]
    assert [i["outlier"] for i in physics.mark_outliers([{"crr": 0.01}, {"crr": 0.05}])] == [False, False]
    assert physics.median_of(items)["crr"] == 0.0165


# ------------------------------------------------------------------ Server


def ride_file(samples: list[Sample], ride_id: int, start: int) -> bytes:
    rows = [{
        72: s.timestamp - T0 + start, 7: round(s.speed_kmh * 100), 8: s.assist_level, 13: round(s.rider_power_w * 100),
        14: round(s.motor_power_w * 100), 15: round(s.total_power_w * 100), 23: s.distance_m, 31: s.latitude, 32: s.longitude,
        39: round(s.altitude_m * 100), 41: round(s.temperature_c * 100), 42: round(s.pressure_kpa * 100), 71: s.battery_pct,
    } for s in samples]
    return ride(rows, start=start, end=start + len(rows), distance=samples[-1].distance_m, ride_id=ride_id)


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai, "_post", lambda *a, **k: pytest.fail("echtes Netz"))
    app = create_app(load_settings(tmp_path / "data"))
    app.state.ai_paused = True
    with TestClient(app) as c:
        yield c


@pytest.fixture
def store(client):
    return client.app.state.store


def add(store, ride_id: int, day: int, **kw) -> int:
    samples = make_samples(n=kw.pop("n", 900), seed=ride_id, **kw)
    start = T0 + day * 86400
    store.add_file(f"ride_rec/4242/ride_rec_{ride_id}.proto", ride_file(samples, ride_id, start))
    return next(r["id"] for r in store.list_rides() if r["ride_id"] == ride_id)


def test_endpoints_hint_without_mass(client, store):
    pk = add(store, 1, 0)
    for url in (f"/api/v1/rides/{pk}/physics", "/api/v1/physics/compare"):
        r = client.get(url)
        assert r.status_code == 409 and r.json()["detail"]["error"] == "mass_missing"
        assert "Systemgewicht" in r.json()["detail"]["message"]


def test_settings_roundtrip_and_validation(client):
    assert client.get("/api/v1/settings/physics").json() == {"mass_kg": None, "cda": 0.7, "cda_custom": False, "battery_wh": None}
    r = client.put("/api/v1/settings/physics", json={"mass_kg": 132, "cda": None, "battery_wh": 800})
    assert r.status_code == 200 and r.json()["mass_kg"] == 132 and r.json()["cda_custom"] is False
    r = client.put("/api/v1/settings/physics", json={"mass_kg": 132, "cda": 0.55, "battery_wh": None})
    assert r.json()["cda"] == 0.55 and r.json()["cda_custom"] is True and r.json()["battery_wh"] is None
    for bad in ({"mass_kg": 5}, {"mass_kg": "schwer"}, {"mass_kg": 100, "cda": 3}, {"mass_kg": True}):
        assert client.put("/api/v1/settings/physics", json=bad).status_code == 400
    assert client.get("/api/v1/settings/physics").json()["mass_kg"] == 132


def test_settings_page_has_physics_section(client):
    html = client.get("/settings").text
    assert "phys-mass" in html and "Du, Ausrüstung und Bike zusammen." in html
    assert "Nur ändern, wenn du deine Luftwiderstandsfläche kennst." in html
    assert "You, gear and bike together." in client.get("/settings?lang=en").text


def test_ride_physics_endpoint_and_cache(client, store, monkeypatch):
    pk = add(store, 1, 0)
    client.put("/api/v1/settings/physics", json={"mass_kg": MASS})
    calls = []
    real = physics.analyze_physics
    monkeypatch.setattr(physics, "analyze_physics", lambda *a, **k: calls.append(a) or real(*a, **k))
    first = client.get(f"/api/v1/rides/{pk}/physics")
    assert first.status_code == 200 and first.json()["ride_id"] == pk
    assert first.json()["resistance"]["crr"] == pytest.approx(TRUE_CRR, abs=0.003)
    assert client.get(f"/api/v1/rides/{pk}/physics").json() == first.json() and len(calls) == 1
    client.put("/api/v1/settings/physics", json={"mass_kg": MASS + 10})  # neues Gewicht: neu rechnen
    second = client.get(f"/api/v1/rides/{pk}/physics").json()
    assert len(calls) == 2 and second["inputs"]["mass_kg"] == MASS + 10
    assert client.get("/api/v1/rides/999/physics").status_code == 404


def test_compare_endpoint_with_median_and_outlier(client, store):
    client.put("/api/v1/settings/physics", json={"mass_kg": MASS})
    ids = [add(store, 1, 0, crr=0.015), add(store, 2, 1, crr=0.016), add(store, 3, 2, crr=0.017), add(store, 4, 3, crr=0.034)]
    r = client.get("/api/v1/physics/compare")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 4 and [x["id"] for x in body["rides"]] == ids
    assert [x["outlier"] for x in body["rides"]] == [False, False, False, True]
    assert body["median"]["crr"] == pytest.approx(0.0165, abs=0.002)
    first = body["rides"][0]
    assert {"date", "name", "crr", "cda", "flat_power_w_25", "wh_per_km", "motor_share_pct", "climb_rider_w_per_kg", "avg_temp_c"} <= set(first)
    window = client.get("/api/v1/physics/compare?start=1970-01-01&end=1970-01-02").json()
    assert window["count"] == 0 and window["rides"] == []
    assert client.get("/api/v1/physics/compare?start=morgen").status_code == 400


def test_deleting_a_ride_drops_its_cache(client, store):
    pk = add(store, 1, 0)
    client.put("/api/v1/settings/physics", json={"mass_kg": MASS})
    client.get(f"/api/v1/rides/{pk}/physics")
    assert store.db.one("SELECT COUNT(*) AS n FROM ride_physics")["n"] == 1
    assert store.delete_ride(pk)
    assert store.db.one("SELECT COUNT(*) AS n FROM ride_physics")["n"] == 0


def test_mcp_tool_returns_json(client, store):
    token = enable(client)
    names = {t["name"] for t in rpc(client, "tools/list", token=token).json()["result"]["tools"]}
    assert "get_physics" in names
    pk = add(store, 1, 0)
    assert call(client, "get_physics", {"ride_id": pk}, token)["error"] == "mass_missing"
    client.put("/api/v1/settings/physics", json={"mass_kg": MASS})
    one = call(client, "get_physics", {"ride_id": pk}, token)
    assert one["ride_id"] == pk and one["resistance"]["crr"] > 0 and one["is_estimate"] is True
    many = call(client, "get_physics", {}, token)
    assert many["count"] == 1 and many["rides"][0]["id"] == pk and "median" in many
    r = rpc(client, "tools/call", {"name": "get_physics", "arguments": {"ride_id": 999}}, token=token)
    assert r.json()["result"].get("isError")
    forbidden = {"lat", "lon", "latitude", "longitude", "serial", "path", "track", "coordinates"}
    assert not forbidden & set(_keys(one) | _keys(many))


def _keys(o) -> set:
    if isinstance(o, dict):
        return {str(k).lower() for k in o} | {k for v in o.values() for k in _keys(v)}
    if isinstance(o, list):
        return {k for v in o for k in _keys(v)}
    return set()
