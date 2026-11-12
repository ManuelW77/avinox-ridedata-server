import pytest

from avinox.ride_rec import parse_ride
from avinox.summary import climb, summarize
from ridebuilder import ride

T0 = 1_800_000_000


def sample(t, speed=0, cadence=0, rider=0, motor=0, alt=None, dist=0, battery=None, hr=None):
    f = {72: T0 + t, 7: int(speed * 100), 9: int(cadence * 100), 13: int(rider * 100), 14: int(motor * 100), 23: dist}
    if alt is not None:
        f[39] = int(alt * 100)
    if battery is not None:
        f[71] = battery
    if hr is not None:
        f[51] = hr
    return {k: v for k, v in f.items() if v}


def test_basic_summary():
    samples = [sample(t, speed=18, cadence=80, rider=150, motor=300, dist=5 * t, battery=90 - t // 5, hr=120 + t) for t in range(10)]
    samples += [sample(10 + t, dist=50) for t in range(5)]  # Stand
    s = summarize(parse_ride(ride(samples)))
    assert s.samples == 15 and s.duration_s == 15 and s.moving_time_s == 10
    assert s.distance_km == 0.05
    assert s.avg_speed_kmh == pytest.approx(18)
    assert s.avg_rider_power_w == pytest.approx(150) and s.avg_motor_power_w == pytest.approx(300)
    assert s.avg_cadence_rpm == pytest.approx(80)
    assert s.rider_energy_kj == pytest.approx(1.5)
    assert s.motor_energy_wh == pytest.approx(3000 / 3600)
    assert (s.battery_start_pct, s.battery_end_pct) == (90, 89)
    assert s.max_heart_rate_bpm == 129
    assert s.start_time.timestamp() == T0


def test_pause_gap_is_not_counted():
    samples = [sample(0, speed=20, rider=100), sample(1, speed=20, rider=100), sample(600, speed=20, rider=100)]
    s = summarize(parse_ride(ride(samples)))
    assert s.duration_s == 2 and s.moving_time_s == 2
    assert s.rider_energy_kj == pytest.approx(0.2)


def test_climb_ignores_noise():
    assert climb([100, 101, 100, 102, 100, 110, 109, 111, 105]) == (11, 6)


def test_empty_ride():
    s = summarize(parse_ride(ride([])))
    assert s.samples == 0 and s.ascent_m is None and s.avg_speed_kmh is None


def test_climb_counts_full_descent_first():
    assert climb([200, 190, 180, 182, 170, 175]) == (5, 30)
    assert climb([100, 101, 102]) == (0, 0)
