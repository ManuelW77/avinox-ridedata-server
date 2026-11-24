import json

import pytest

from avinox.analysis import analyze, assist_name, best_efforts, histogram
from avinox.buried import parse_buried
from avinox.ride_rec import parse_ride
from ridebuilder import ride
from test_buried import LINES

T0 = 1_800_000_000


def s(t, speed=18.0, rider=100, motor=200, assist=2, gear=5, battery=80):
    return {72: T0 + t, 7: int(speed * 100), 13: rider * 100, 14: motor * 100, 8: assist, 22: gear, 71: battery, 23: int(5 * t)}


def test_histogram_and_names():
    assert histogram([0, 10, 60, 60, 2000], [1, 1, 1, 2, 1], [1, 50, 1000]) == {"<1": 1, "1–50": 1, "50–1000": 3, "≥1000": 1}
    assert (assist_name(2), assist_name(4), assist_name(21), assist_name(9)) == ("trail", "auto", "eigen_21", "stufe_9")
    assert assist_name(21, {21: "Tour"}) == "Tour"


def test_best_efforts():
    values = [0] * 10 + [300] * 5 + [0] * 10
    assert best_efforts(values, [5, 10, 60]) == {"5s": 300, "10s": 150}


def test_analyze_synthetic_ride():
    samples = [s(t, assist=2, gear=5) for t in range(60)]
    samples += [s(60 + t, speed=0, rider=0, motor=0, assist=2, gear=5) for t in range(40)]  # Stopp 40 s
    samples += [s(100 + t, assist=4, gear=7, battery=79) for t in range(30)]
    a = analyze(parse_ride(ride(samples, start=T0, end=T0 + 130)))
    assert a["assist"]["seconds_per_mode"] == {"trail": 100, "auto": 30}
    assert a["assist"]["changes"] == 1
    assert a["gears"]["seconds_per_gear"] == {"5": 100, "7": 30} and a["gears"]["changes"] == 1
    assert a["assist"]["per_mode"]["trail"]["avg_moving_speed_kmh"] == 18
    assert len(a["stops"]) == 1 and a["stops"][0]["duration_s"] == 40
    assert a["energy"]["motor_share_pct"] == pytest.approx(66.7, abs=0.1)
    assert a["battery"]["used_pct"] == 1
    assert a["distributions"]["rider_power_s"]["100–150"] == 90
    assert a["series"]["bucket_s"] == 1 and len(a["series"]["rows"]) == 130
    assert a["device_stats"] is None and a["gps"]["points"] == 0
    json.dumps(a)  # muss serialisierbar sein


def test_series_is_downsampled():
    a = analyze(parse_ride(ride([s(t) for t in range(250)])), series_points=100)
    assert a["series"]["bucket_s"] == 3 and len(a["series"]["rows"]) == 84


def test_analyze_with_device_stats():
    end = T0 + 7712
    data = ride([s(0)], start=T0, end=end, count=7643, distance=18368)
    sessions = parse_buried(LINES.replace("2026-11-5-22:58:43", "2027-1-15-16:8:32"))
    a = analyze(parse_ride(data), sessions)
    assert a["device_stats"]["seconds_per_assist_mode"]["trail"] == 4933
    assert a["device_stats"]["session"]["boot_count"] == 7
    json.dumps(a)


def test_custom_mode_names():
    samples = [s(t, assist=21) for t in range(10)] + [s(10 + t, assist=20) for t in range(5)]
    a = analyze(parse_ride(ride(samples)), custom_names={21: "Tour"})
    assert a["assist"]["seconds_per_mode"] == {"eigen_20": 5, "Tour": 10}


def test_share_gradient_cadence():
    def g(t, grad, rider, motor, cad=0, gear=5, speed=18.0):
        d = s(t, speed=speed, rider=rider, motor=motor, gear=gear)
        d[40] = int(grad * 100) & 0xFFFF if grad >= 0 else (int(grad * 100) + 65536)
        d[9] = int(cad * 100)
        return d
    samples = [g(t, 0, 0, 200) for t in range(10)]            # nur Motor
    samples += [g(10 + t, 0, 100, 100, cad=80) for t in range(20)]  # 50 %
    samples += [g(30 + t, 5, 150, 50, cad=60) for t in range(10)]   # 25 %, Steigung 3-6
    a = analyze(parse_ride(ride(samples, start=T0, end=T0 + 40)))
    bg = a["by_gradient"]
    assert bg["-1–1"]["seconds"] == 30 and bg["-1–1"]["avg_rider_power_w"] == 67
    assert bg["3–6"] == {"seconds": 10, "avg_rider_power_w": 150, "avg_motor_power_w": 50, "avg_speed_kmh": 18.0}
    assert a["gears"]["per_gear"]["5"]["avg_cadence_rpm"] == 73
    json.dumps(a)


def test_motor_load_per_gear():
    def g(t, speed=18.0, motor=400, cad=40, gear=9):
        d = s(t, speed=speed, motor=motor, gear=gear)
        d[9] = int(cad * 100)
        return d
    samples = [g(t, motor=100, cad=80) for t in range(5)]        # nicht hoch
    samples += [g(5 + t) for t in range(4)]                      # 4 s schwer: zählt nicht
    samples += [g(9 + t, motor=100, cad=80) for t in range(2)]
    samples += [g(11 + t) for t in range(6)]                     # 6 s schwer: zählt
    samples += [g(17 + t, speed=0.0, motor=0, cad=0) for t in range(3)]  # Halt
    samples += [g(20 + t, gear=7, cad=80) for t in range(3)]     # Anfahren in Gang 7, hohe Last, nicht schwer
    a = analyze(parse_ride(ride(samples, start=T0, end=T0 + 25)))
    pg = a["gears"]["per_gear"]
    assert pg["9"]["high_motor_seconds"] == 10
    assert pg["9"]["grinding_seconds"] == 6
    assert pg["7"]["high_motor_seconds"] == 3 and pg["7"]["grinding_seconds"] == 0
    assert a["gears"]["starts_per_gear"] == {"7": 1}


def test_load_hints():
    def g(t, gear, cad=40, motor=400, speed=18.0):
        d = s(t, speed=speed, motor=motor, gear=gear)
        d[9] = int(cad * 100)
        return d
    # Gang 2 (leichtester) und Gang 9: je 70 s schwer; Gang 9 dazu 3 Anfahrten
    samples = [g(t, 2) for t in range(70)] + [g(70 + t, 9) for t in range(70)]
    t0 = 140
    for k in range(3):
        samples += [g(t0, 9, speed=0.0, motor=0, cad=0), g(t0 + 1, 9, cad=80, motor=0)]
        t0 += 2
    a = analyze(parse_ride(ride(samples, start=T0, end=T0 + t0)))
    assert a["gears"]["load_hints"] == {"grinding": [9], "heavy_starts": [9]}
    # nur ein Gang genutzt: nichts melden
    a = analyze(parse_ride(ride([g(t, 3) for t in range(70)], start=T0, end=T0 + 70)))
    assert a["gears"]["load_hints"] == {"grinding": [], "heavy_starts": []}


def test_load_hints_ignore_short_gears():
    def g(t, gear, speed=18.0, cad=40):
        d = s(t, speed=speed, motor=400, gear=gear)
        d[9] = int(cad * 100)
        return d
    samples = [g(t, 2) for t in range(40)] + [g(40 + t, 4) for t in range(70)]
    t0 = 110
    for k in range(3):  # 3 Anfahrten in Gang 6, aber nur ca. 6 s Fahrzeit
        samples += [g(t0, 6, speed=0.0), g(t0 + 1, 6, cad=80), g(t0 + 2, 6, cad=80)]
        t0 += 3
    samples += [g(t0 + k, 8, cad=80) for k in range(2)]
    a = analyze(parse_ride(ride(samples, start=T0, end=T0 + t0 + 2)))
    assert a["gears"]["starts_per_gear"]["6"] == 3
    assert a["gears"]["load_hints"] == {"grinding": [4], "heavy_starts": []}
