from avinox.battery import health


def S(time=None, start=50, end=40, st=15, et=18, mx=20, cur=5, mn_c=3800, mx_c=4000, delta=8, temp_s=None, limit=False, **kw):
    return {
        "time": time, "start_pct": start, "end_pct": end, "start_temp_c": st, "end_temp_c": et, "max_temp_c": mx,
        "max_current_a": cur, "min_cell_mv": mn_c, "max_cell_mv": mx_c, "max_cell_delta_mv": delta,
        "temp_s": temp_s or {}, "thermal_limit": limit, **kw,
    }


def test_charge_detection_and_unknown_start():
    r = health([S(start=80, end=40), S(start=70, end=60, st=12), S(start=0, end=90, st=0, et=0)], [])
    assert r["charge_count"] == 2
    assert r["charges"][0] == {"from_pct": 40, "to_pct": 70, "time": None, "temp_c": 12}
    assert r["charges"][1]["to_pct"] is None and r["charges"][1]["min_to_pct"] == 90


def test_no_charge_small_rise():
    assert health([S(start=80, end=40), S(start=42, end=30)], [])["charge_count"] == 0


def test_cycles_ignore_unknown_zero():
    r = health([S(start=100, end=50), S(start=0, end=0, st=0, et=0), S(start=80, end=30)], [])
    # 100->50 (50), 50->80 Ladung, 80->30 (50); unbekannte Werte übersprungen
    assert r["discharged_pct"] == 100 and r["full_cycles"] == 1.0


def test_rest_balance_filter():
    ss = [S(mx_c=4100, cur=1, delta=5), S(mx_c=4100, cur=10, delta=30), S(mx_c=3900, cur=1, delta=40), S(mx_c=4150, cur=2, delta=25)]
    rb = health(ss, [])["rest_balance"]
    assert [p["delta_mv"] for p in rb["points"]] == [5, 25]
    assert rb["first_mv"] == 5 and rb["latest_mv"] == 25
    assert any(h["key"] == "balance" for h in health(ss, [])["hints"])


def test_full_storage_needs_known_times():
    a = S(time="2025-05-01T10:00:00+00:00", end=100)
    b = S(time="2025-05-10T10:00:00+00:00", start=100, end=90)
    assert health([a, b], [])["full_storage"][0]["days"] == 9.0
    unknown = S(time="2001-01-01T00:00:00+00:00", start=100, end=90)
    assert health([a, unknown], [])["full_storage"] == []
    assert health([a, dict(b, time=None)], [])["full_storage"] == []
    near = S(time="2025-05-02T10:00:00+00:00", start=100)
    assert health([a, near], [])["full_storage"] == []


def test_soh_change():
    ws = [5, 5, 9, 5, 5, 4, 4, 1, 4, 4]  # Ausreißer 9 und 1 fängt der Median ab
    rides = [{"time": f"2025-{i:02d}-01T00:00:00+00:00", "motor_wh_per_pct": w, "used_pct": 20} for i, w in enumerate(ws, 1)]
    soh = health([], rides)["soh"]
    assert soh["points"][0]["wh"] == 500 and soh["change_pct"] == -20.0
    assert health([], rides[:9])["soh"]["change_pct"] is None
    few = [dict(r, used_pct=6) for r in rides]
    assert health([], few)["soh"]["points"] == []


def test_hints_and_ok():
    assert health([S()], [])["hints"] == [{"level": "ok", "key": "ok", "params": {}}]
    r = health([S(end=5), S(start=60, end=50, st=2, et=3, mx=3, limit=True)], [])
    keys = {h["key"] for h in r["hints"]}
    assert {"deep", "coldcharge", "limit"} <= keys
    assert r["deep_discharge"]["count"] == 1 and r["temperature"]["cold_sessions"] == 1


def test_temperature_and_sag():
    ss = [S(mx=0, st=0, et=0), S(mx=46, temp_s={"<30": 10, "50": 20, ">60": 5}), S(cur=20, mx_c=4000, mn_c=3800)]
    r = health(ss, [])
    assert r["temperature"]["cold_sessions"] == 0
    assert r["temperature"]["hot_sessions"] == 1 and r["temperature"]["seconds_over_50"] == 25
    assert r["sag"] == [{"time": None, "mv_per_a": 10.0}]


def R(delta, time=None, **kw):
    return S(time=time, mx_c=4100, cur=1, delta=delta, **kw)


def keys(r):
    return [h["key"] for h in r["hints"]]


def test_balance_crit_and_status_check():
    r = health([R(10), R(55)], [])
    assert r["hints"][0] == {"level": "crit", "key": "balance_crit", "params": {"mv": 55}}
    assert "balance" not in keys(r) and "balance_rising" not in keys(r)
    assert r["status"] == "check"
    assert r["rest_balance"]["warn_mv"] == 20 and r["rest_balance"]["crit_mv"] == 50


def test_balance_warn_boundaries():
    assert "balance" not in keys(health([R(19)], []))
    assert "balance" in keys(health([R(20)], []))
    assert "balance_crit" in keys(health([R(50)], []))


def test_balance_rising():
    r = health([R(18), R(22), R(30)], [])
    assert "balance_rising" in keys(r) and "balance" in keys(r)
    assert "balance_rising" not in keys(health([R(30), R(22), R(25)], []))
    assert "balance_rising" not in keys(health([R(10), R(12), R(15)], []))


def test_balance_down():
    r = health([R(30), R(22)], [])
    assert {"level": "info", "key": "balance_down", "params": {"mv": 22}} in r["hints"]
    assert "balance_down" not in keys(health([R(30), R(27)], []))
    assert "balance_down" not in keys(health([R(15), R(5)], []))


def test_fullcharge_by_days():
    a = S(time="2025-01-01T10:00:00+00:00", start=100, end=60)
    b = S(time="2025-03-01T10:00:00+00:00", start=70, end=50)
    r = health([a, b], [])
    h = [x for x in r["hints"] if x["key"] == "fullcharge_days"]
    assert h and h[0]["level"] == "info" and h[0]["params"]["days"] == 59
    assert r["last_full"]["time"] == a["time"]
    near = S(time="2025-01-20T10:00:00+00:00", start=70, end=50)
    assert "fullcharge_days" not in keys(health([a, near], []))


def test_fullcharge_by_rides():
    full = S(start=100, end=80)
    rides = [S(start=70, end=60) for _ in range(30)]
    r = health([full] + rides, [])
    assert {"level": "info", "key": "fullcharge_rides", "params": {"n": 30}} in r["hints"]
    assert r["last_full"] == {"time": None, "rides_since": 30}
    assert "fullcharge_rides" not in keys(health([full] + rides[:29], []))
    assert "fullcharge_rides" in keys(health(rides, []))  # ohne Vollladung: insgesamt


def test_status_good_watch_and_sorting():
    assert health([S()], [])["status"] == "good"
    assert health([R(25)], [])["status"] == "watch"
    cold = S(st=2, et=3, mx=3)
    r = health([cold, R(60), S(end=5)], [])
    levels = [h["level"] for h in r["hints"]]
    assert levels == sorted(levels, key=["crit", "warn", "info", "ok"].index)
    assert levels[0] == "crit" and r["status"] == "check"
