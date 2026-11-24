"""Format-Wächter – nur synthetische Daten."""

import json
import struct

from avinox import formatwatch
from avinox.ride_rec import MAGIC
from ridebuilder import frame, ride
from test_server import RIDE_PATH, client, clean_env, put, settings  # noqa: F401
from test_track import T0, gps_sample

HMS_PATH = "hms_rec/hms_current_storage.proto"
BURIED_PATH = "buried_rec/buried_file_cur.json"


def kinds(findings):
    return {(f.kind, f.key) for f in findings}


def hms_bytes(*codes):
    head = struct.pack("<II", MAGIC, 1) + b"TESTSERIAL0001".ljust(18, b"\0") + b"\0\0\0\0"
    data = head[:30] + b"\0\0"
    for i, c in enumerate(codes):
        data += frame({1: c, 4: 1_800_000_000 + i}, frame_type=0x01EB)
    return data


def buried_bytes(*objs):
    return "\n".join(json.dumps(o) for o in objs).encode()


def test_ride_unknown_field():
    data = ride([{**gps_sample(t, 5 * t), 76: 7} for t in range(3)], start=T0, end=T0 + 3)
    f = formatwatch.check("ride", RIDE_PATH, data)
    assert kinds(f) == {("ride_field", "76")} and f[0].detail.startswith("3|")


def test_ride_known_fields_clean():
    data = ride([gps_sample(t, 5 * t) for t in range(3)], start=T0, end=T0 + 3)
    assert formatwatch.check("ride", RIDE_PATH, data) == []


def test_ride_unknown_frame_type():
    data = ride([], start=T0, end=T0 + 1) + frame({6: 1}, frame_type=0x0BAD)
    assert ("frame_type", "0x0BAD") in kinds(formatwatch.check("ride", RIDE_PATH, data))


def test_buried():
    known = {"event_id": "rider_cadence", "time_now": "20261105T145843", "average": 80, "max": 100}
    assert formatwatch.check("buried", BURIED_PATH, buried_bytes({"general_msg": {"boot_times": 3}}, known)) == []
    life = {"event_id": "battery_life_data", "time_now": "x", "total_trip": 1, "total_climb": 2}
    assert formatwatch.check("buried", BURIED_PATH, buried_bytes(life)) == []
    new = [{"event_id": "battery_xyz", "time_now": "x", "a": 1}, {**known, "extra_key": 5}]
    assert kinds(formatwatch.check("buried", BURIED_PATH, buried_bytes(*new))) == {
        ("buried_event", "battery_xyz"), ("buried_key", "rider_cadence.extra_key")}


def test_hms():
    assert formatwatch.check("hms", HMS_PATH, hms_bytes(0xABAB)) == []
    f = formatwatch.check("hms", HMS_PATH, hms_bytes(0xABAB, 1234))
    assert len(f) == 1 and f[0].kind == "hms" and f[0].key.startswith("1234@") and f[0].detail == "1234|0x04D2"


def test_upsert_counts_and_sync_page(client, settings):
    store = client.app.state.store
    data = ride([{**gps_sample(t, 5 * t), 76: 7} for t in range(3)], start=T0, end=T0 + 3)
    assert put(client, settings, data).status_code == 200
    put(client, settings, hms_bytes(1234), path=HMS_PATH)
    other = ride([{**gps_sample(t, 5 * t), 76: 7} for t in range(4)], start=T0, end=T0 + 4)
    put(client, settings, other, path="ride_rec/4242/ride_rec_1001.proto")
    row = [r for r in store.format_findings() if r["kind"] == "ride_field"][0]
    assert row["count"] == 2 and row["seen"] == 0
    assert store.format_findings_unseen()["hms"]["count"] == 1
    html = client.get("/sync").text
    assert "Neues Datenfeld 76" in html and "Fehlercode 1234 (0x04D2)" in html
    assert 'id="findings"' in html
    assert client.post("/api/v1/sync/findings/seen", follow_redirects=False).status_code == 303
    assert store.format_findings_unseen()["format"]["count"] == 0
    assert all(r["seen"] == 1 for r in store.format_findings())
