"""Erzeugt synthetische Fahrtdateien für Tests (keine echten Fahrdaten im Repo)."""

from __future__ import annotations

import struct

from avinox.ride_rec import FRAME_TYPE_SAMPLE, HEADER_SIZE, MAGIC


def varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode(fields: dict[int, int | float | bytes]) -> bytes:
    """int -> varint, float -> fixed64 (double), bytes mit Länge 4 -> fixed32."""
    out = bytearray()
    for number, value in fields.items():
        if isinstance(value, float):
            out += varint(number << 3 | 1) + struct.pack("<d", value)
        elif isinstance(value, bytes):
            out += varint(number << 3 | 5) + value
        else:
            out += varint(number << 3 | 0) + varint(value)
    return bytes(out)


def header(ride_id=1, start=1_800_000_000, end=1_800_000_010, distance=0, ascent=0.0, descent=0.0, count=0, serial=b"TESTSERIAL0001", owner=b"", preview=(), stats=()):
    buf = bytearray(HEADER_SIZE)
    struct.pack_into("<IHIIIIff", buf, 0, MAGIC, 8, ride_id, start, end, distance, ascent, descent)
    buf[110 : 110 + len(serial)] = serial
    buf[128] = len(owner)
    buf[129 : 129 + len(owner)] = owner
    for i, (lat, lon) in enumerate(preview):
        struct.pack_into("<ii", buf, 30 + 8 * i, round(lat * 1e7), round(lon * 1e7))
    if stats:
        struct.pack_into("<19f", buf, 161, *stats)
    struct.pack_into("<I", buf, 245, count)
    return bytes(buf)


def frame(fields: dict, frame_type: int = FRAME_TYPE_SAMPLE) -> bytes:
    body = encode(fields)
    return struct.pack("<HH", frame_type, len(body)) + body + b"\0\0"


def ride(samples: list[dict], **header_args) -> bytes:
    header_args.setdefault("count", len(samples))
    return header(**header_args) + b"".join(frame(s) for s in samples)
