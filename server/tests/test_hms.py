import struct

from avinox.hms import parse_hms
from avinox.ride_rec import MAGIC
from ridebuilder import frame


def test_parse_hms():
    head = struct.pack("<II", MAGIC, 1) + b"TESTSERIAL0001".ljust(18, b"\0") + b"\0\0\0\0"
    data = head[:30] + b"\0\0" + frame({1: 43947, 4: 1_800_000_000}, frame_type=0x01EB)
    entries = parse_hms(data)
    assert len(entries) == 1 and entries[0].code == 43947 and entries[0].time.year == 2027
    assert parse_hms(b"kaputt") == []
