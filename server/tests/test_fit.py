import io

import fitdecode

from avinox.fit import to_fit
from avinox.ride_rec import parse_ride
from ridebuilder import ride
from test_track import LAT, LON, T0, gps_sample, straight_ride


def read(data: bytes):
    frames = []
    with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
        for fr in reader:
            if isinstance(fr, fitdecode.FitDataMessage):
                frames.append(fr)
    return frames


def by_name(frames, name):
    return [f for f in frames if f.name == name]


def test_fit_roundtrip():
    r = straight_ride(30)
    frames = read(to_fit(r))
    records = by_name(frames, "record")
    assert len(records) == len([s for s in r.samples if s.timestamp is not None]) == 30
    first = records[0]
    assert abs(first.get_value("position_lat") * 180 / 2**31 - LAT) < 1e-5
    assert abs(first.get_value("position_long") * 180 / 2**31 - LON) < 1e-5
    assert abs(first.get_value("enhanced_altitude") - 500) < 0.5
    assert first.get_value("power") == 150
    assert first.get_value("heart_rate") == 130
    assert first.get_value("cadence") is not None
    assert first.has_field("motor_power")
    session = by_name(frames, "session")[0]
    assert session.get_value("total_distance") is not None
    assert session.get_value("sport") == "cycling"
    assert session.get_value("sub_sport") == "e_bike_mountain"
    r.samples[0].rider_torque_nm = 12.5
    r.samples[0].pressure_kpa = 95.3
    first = by_name(read(to_fit(r)), "record")[0]
    assert abs(first.get_value("rider_torque") - 12.5) < 1e-4
    assert first.get_value("pressure") == 953
    assert len(by_name(frames, "lap")) == 1 and len(by_name(frames, "activity")) == 1
    assert len(by_name(frames, "event")) == 2


def test_fit_without_gps_and_wallclock():
    r = parse_ride(ride([{72: 100 + t, 7: 1800} for t in range(5)], start=100, end=105))
    assert not r.header.has_wallclock
    records = by_name(read(to_fit(r)), "record")
    assert len(records) == 5 and records[0].get_value("position_lat", fallback=None) is None
    assert records[1].get_value("timestamp") is not None


def test_fit_skips_samples_without_timestamp():
    r = parse_ride(ride([gps_sample(0, 0), {6: 1}, gps_sample(2, 10)], start=T0, end=T0 + 3))
    assert len(by_name(read(to_fit(r)), "record")) == 2
