import pytest

from avinox.ride_rec import RideRecError, parse_ride
from ridebuilder import frame, header, ride


def test_header_fields():
    r = parse_ride(ride([{6: 1}], ride_id=1234, start=1_800_000_000, end=1_800_000_600, distance=4200, ascent=12.0, descent=8.0))
    h = r.header
    assert (h.ride_id, h.distance_m, h.duration_s, h.ascent_m, h.descent_m) == (1234, 4200, 600, 12.0, 8.0)
    assert h.serial == "TESTSERIAL0001"
    assert h.has_wallclock and h.start_time.year == 2027
    assert r.warnings == []


def test_boot_time_has_no_wallclock():
    h = parse_ride(ride([], start=4790, end=5045)).header
    assert not h.has_wallclock and h.start_time is None


def test_sample_scaling():
    s = parse_ride(ride([{
        6: 5, 7: 2345, 8: 3, 9: 8550, 10: 4000, 11: 3000, 12: 7000, 13: 21000, 14: 45000, 15: 66000,
        22: 7, 23: 1500, 31: 47.5, 32: 11.25, 39: 52345, 40: -350, 41: 1850, 42: 10149,
        51: 142, 52: 12345, 71: 88, 72: 1_800_000_001, 73: 99000, 75: 2, 99: 1,
    }])).samples[0]
    assert s.seq == 5 and s.timestamp == 1_800_000_001
    assert s.speed_kmh == 23.45 and s.assist_level == 3 and s.cadence_rpm == 85.5
    assert (s.motor_torque_nm, s.rider_torque_nm, s.total_torque_nm) == (40, 30, 70)
    assert (s.rider_power_w, s.motor_power_w, s.total_power_w) == (210, 450, 660)
    assert s.gear == 7 and s.distance_m == 1500
    assert (s.latitude, s.longitude) == (47.5, 11.25)
    assert s.altitude_m == 523.45 and s.gradient_pct == -3.5
    assert s.temperature_c == 18.5 and s.pressure_kpa == 101.49
    assert s.heart_rate_bpm == 142 and s.calories_kcal == 12.345
    assert s.battery_pct == 88 and s.odometer_m == 99000 and s.event == 2
    assert s.extra == {99: 1}


def test_missing_fields():
    s = parse_ride(ride([{6: 1, 41: -99900}])).samples[0]
    # Protobuf lässt Nullwerte weg: Leistung & Co. sind dann 0 ...
    assert s.speed_kmh == 0 and s.rider_power_w == 0 and s.assist_level == 0 and s.distance_m == 0
    # ... Messwerte ohne Signal bleiben unbekannt
    assert s.altitude_m is None and s.latitude is None and s.heart_rate_bpm is None
    assert s.temperature_c is None and s.pressure_kpa is None


def test_zero_coordinates_mean_no_fix():
    s = parse_ride(ride([{31: 0.0, 32: 0.0}])).samples[0]
    assert s.latitude is None and s.longitude is None


@pytest.mark.parametrize("data", [b"", b"\0" * 300, header()[:100]])
def test_invalid_file(data):
    with pytest.raises(RideRecError):
        parse_ride(data)


def test_truncated_last_frame_keeps_earlier_samples():
    data = ride([{6: 1}, {6: 2}])
    r = parse_ride(data[:-3])
    assert [s.seq for s in r.samples] == [1]
    assert any("abgeschnitten" in w for w in r.warnings)


def test_unknown_frame_type_is_skipped():
    data = header(count=2) + frame({6: 1}) + frame({6: 9}, frame_type=0x1234) + frame({6: 2})
    r = parse_ride(data)
    assert [s.seq for s in r.samples] == [1, 2]
    assert any("0x1234" in w for w in r.warnings)


def test_extra_final_frame_is_no_warning():
    assert parse_ride(ride([{6: 1}, {6: 2}], count=1)).warnings == []


def test_owner_and_preview():
    h = parse_ride(ride([], owner=b"424242", preview=[(47.1234567, 11.7654321), (47.2, 11.8)])).header
    assert h.owner_id == "424242"
    assert h.preview == [(47.1234567, 11.7654321), (47.2, 11.8)]
    assert parse_ride(ride([])).header.preview == []


def test_header_stats_order():
    stats = parse_ride(ride([], stats=[float(i) for i in range(1, 19)] + [305244.0])).header.stats
    assert (stats.avg_gradient_pct, stats.max_gradient_pct, stats.avg_speed_kmh, stats.max_speed_kmh) == (1, 2, 3, 4)
    assert (stats.avg_total_power_w, stats.max_total_power_w, stats.avg_total_torque_nm, stats.max_total_torque_nm) == (5, 6, 7, 8)
    assert (stats.avg_motor_power_w, stats.max_motor_power_w, stats.avg_motor_torque_nm, stats.max_motor_torque_nm) == (9, 10, 11, 12)
    assert (stats.avg_rider_power_w, stats.max_rider_power_w, stats.avg_rider_torque_nm, stats.max_rider_torque_nm) == (13, 14, 15, 16)
    assert (stats.avg_cadence_rpm, stats.max_cadence_rpm, stats.calories_kcal) == (17, 18, 305.244)
