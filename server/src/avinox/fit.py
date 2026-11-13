"""FIT-Export (Garmin FIT-Protokoll) einer Fahrt mit allen vorhandenen Daten.

Reiner Python-Encoder ohne Abhängigkeiten. Die Standardfelder (Position, Höhe,
Leistung, Puls, ...) liegen in den ``record``-Nachrichten, alles ohne
FIT-Gegenstück (Motorleistung, Drehmomente, Unterstützungsstufe, Gang, Akku,
Luftdruck) als Entwicklerfelder.

Zeit: FIT zählt Sekunden seit 1989-12-31 (Unix - 631065600). Fahrten ohne Uhrzeit
(``header.has_wallclock`` falsch) haben nur Sekunden seit Gerätestart; dort wird
relativ ab der FIT-Epoche exportiert (FIT-Zeit = ``t - header.start``).
"""

from __future__ import annotations

import struct

from .ride_rec import Ride

FIT_EPOCH_OFFSET = 631_065_600
_SEMICIRCLE = 2**31 / 180

# Basistypen: (Code, struct-Format, Invalid-Wert)
_ENUM = (0x00, "B", 0xFF)
_SINT8 = (0x01, "b", 0x7F)
_UINT8 = (0x02, "B", 0xFF)
_SINT16 = (0x83, "h", 0x7FFF)
_UINT16 = (0x84, "H", 0xFFFF)
_SINT32 = (0x85, "i", 0x7FFFFFFF)
_UINT32 = (0x86, "I", 0xFFFFFFFF)
_FLOAT32 = (0x88, "f", None)
"""float32: Invalid = alle Bits gesetzt, siehe ``_pack``."""

_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


def fit_crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


# Entwicklerfelder: (Nummer, Name, Einheit, Basistyp, Skalierung, Attribut, Faktor)
# Skalierung bleibt 1: viele Decoder ignorieren sie bei Entwicklerfeldern. Darum Drehmomente
# als float32 (Faktor None = unverändert) und Luftdruck in hPa (kPa * 10).
_DEV_FIELDS = (
    (0, "motor_power", "W", _UINT16, 1, "motor_power_w", 1),
    (1, "total_power", "W", _UINT16, 1, "total_power_w", 1),
    (2, "motor_torque", "Nm", _FLOAT32, 1, "motor_torque_nm", None),
    (3, "rider_torque", "Nm", _FLOAT32, 1, "rider_torque_nm", None),
    (4, "total_torque", "Nm", _FLOAT32, 1, "total_torque_nm", None),
    (5, "assist_level", "", _UINT8, 1, "assist_level", 1),
    (6, "gear", "", _UINT8, 1, "gear", 1),
    (7, "battery", "%", _UINT8, 1, "battery_pct", 1),
    (8, "pressure", "hPa", _UINT16, 1, "pressure_kpa", 10),
)


class _Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def definition(self, local: int, global_num: int, fields: list[tuple[int, tuple]], dev: list[tuple[int, int]] | None = None) -> None:
        """fields: (Feldnummer, Typ-Tupel); String-Felder als (Nr, ('str', Länge))."""
        self.buf.append(0x40 | local | (0x20 if dev else 0))
        self.buf += struct.pack("<BBHB", 0, 0, global_num, len(fields))
        for num, typ in fields:
            if typ[0] == "str":
                self.buf += bytes((num, typ[1], 0x07))
            elif typ[0] == "bytes":
                self.buf += bytes((num, typ[1], 0x0D))
            else:
                self.buf += bytes((num, struct.calcsize("<" + typ[1]), typ[0]))
        if dev:
            self.buf.append(len(dev))
            for num, size in dev:
                self.buf += bytes((num, size, 0))

    def data(self, local: int, fields: list[tuple[tuple, int | bytes | None]]) -> None:
        self.buf.append(local)
        for typ, value in fields:
            if typ[0] in ("str", "bytes"):
                raw = value if isinstance(value, bytes) else (value or "").encode("ascii", "replace")
                self.buf += raw[: typ[1]].ljust(typ[1], b"\x00")
            else:
                self.buf += struct.pack("<" + typ[1], typ[2] if value is None else value)

    def message(self, local: int, global_num: int, items: list[tuple[int, tuple, int | bytes | None]]) -> None:
        self.definition(local, global_num, [(n, t) for n, t, _ in items])
        self.data(local, [(t, v) for _, t, v in items])


def _scaled(value: float | None, scale: float, offset: float = 0.0, lo: int = 0, hi: int = 0xFFFFFFFE) -> int | None:
    if value is None:
        return None
    return max(lo, min(hi, round((value + offset) * scale)))


def to_fit(ride: Ride, name: str | None = None) -> bytes:
    """Fahrt als FIT-Aktivität (Radfahren, E-MTB). ``name`` ist derzeit nur für den Aufrufer
    gedacht (FIT-Aktivitäten haben kein Namensfeld) und wird nicht geschrieben."""
    header = ride.header
    samples = [s for s in ride.samples if s.timestamp is not None]

    def ft(t: int) -> int:
        return t - FIT_EPOCH_OFFSET if header.has_wallclock else max(0, t - header.start)

    start_t = ft(header.start)
    end_ts = header.end if header.end >= header.start else (samples[-1].timestamp if samples else header.start)
    end_t = ft(end_ts)
    elapsed = max(0, end_ts - header.start)
    w = _Writer()

    # file_id
    w.message(0, 0, [(0, _ENUM, 4), (1, _UINT16, 255), (2, _UINT16, 0), (4, _UINT32, start_t)])

    # Entwicklerfeld-Beschreibungen
    w.message(1, 207, [(1, ("bytes", 16), b"avinox-ridedata"), (3, _UINT8, 0)])
    w.definition(2, 206, [(0, _UINT8), (1, _UINT8), (2, _UINT8), (3, ("str", 16)), (8, ("str", 8)), (6, _UINT8), (14, _UINT16)])
    for num, fname, unit, typ, scale, _attr, _f in _DEV_FIELDS:
        w.data(2, [(_UINT8, 0), (_UINT8, num), (_UINT8, typ[0]), (("str", 16), fname), (("str", 8), unit), (_UINT8, scale), (_UINT16, 20)])

    # event: Timer-Start
    event_fields = [(253, _UINT32), (0, _ENUM), (1, _ENUM)]
    w.definition(3, 21, event_fields)
    w.data(3, [(_UINT32, start_t), (_ENUM, 0), (_ENUM, 0)])

    # record
    rec_fields = [
        (253, _UINT32), (0, _SINT32), (1, _SINT32), (78, _UINT32), (5, _UINT32), (73, _UINT32),
        (4, _UINT8), (3, _UINT8), (7, _UINT16), (13, _SINT8), (9, _SINT16), (33, _UINT16),
    ]
    w.definition(4, 20, rec_fields, [(num, struct.calcsize("<" + typ[1])) for num, _n, _u, typ, _s, _a, _f in _DEV_FIELDS])
    rec_types = [t for _, t in rec_fields]
    for s in samples:
        has_pos = s.latitude is not None and s.longitude is not None
        values = [
            ft(s.timestamp),
            round(s.latitude * _SEMICIRCLE) if has_pos else None,
            round(s.longitude * _SEMICIRCLE) if has_pos else None,
            _scaled(s.altitude_m, 5, 500),
            _scaled(s.distance_m, 100),
            _scaled(s.speed_kmh / 3.6, 1000),
            _scaled(s.cadence_rpm, 1, hi=254),
            _scaled(s.heart_rate_bpm, 1, hi=254) if s.heart_rate_bpm else None,
            _scaled(s.rider_power_w, 1, hi=0xFFFE),
            _scaled(s.temperature_c, 1, lo=-127, hi=126),
            _scaled(s.gradient_pct, 100, lo=-32767, hi=32766),
            _scaled(s.calories_kcal, 1, hi=0xFFFE),
        ]
        w.buf.append(4)
        for typ, v in zip(rec_types, values):
            w.buf += struct.pack("<" + typ[1], typ[2] if v is None else v)
        for _n, _name, _u, typ, _s, attr, factor in _DEV_FIELDS:
            v = getattr(s, attr)
            if typ is _FLOAT32:
                w.buf += b"\xff\xff\xff\xff" if v is None else struct.pack("<f", v)
                continue
            hi = 0xFFFE if typ is _UINT16 else 254
            w.buf += struct.pack("<" + typ[1], typ[2] if v is None else _scaled(v, factor, hi=hi))

    # event: Timer-Stop
    w.data(3, [(_UINT32, end_t), (_ENUM, 0), (_ENUM, 4)])

    # Zusammenfassung
    def avg_max(values: list[float]) -> tuple[int | None, int | None]:
        return (round(sum(values) / len(values)), round(max(values))) if values else (None, None)

    avg_pow, max_pow = avg_max([s.rider_power_w for s in samples])
    avg_cad, max_cad = avg_max([s.cadence_rpm for s in samples])
    avg_hr, max_hr = avg_max([s.heart_rate_bpm for s in samples if s.heart_rate_bpm])
    stats = header.stats
    distance = header.distance_m or (samples[-1].distance_m if samples else 0)
    calories = _scaled(stats.calories_kcal if stats.calories_kcal == stats.calories_kcal else None, 1, hi=0xFFFE)
    avg_speed = _scaled(stats.avg_speed_kmh / 3.6, 1000)
    max_speed = _scaled(stats.max_speed_kmh / 3.6, 1000)
    ascent = _scaled(header.ascent_m, 1, hi=0xFFFE)
    descent = _scaled(header.descent_m, 1, hi=0xFFFE)
    ms = elapsed * 1000

    def summary(local: int, num: int, extra_head: list, ids: dict[str, int], speed_ids: tuple[int, int], tail: list) -> None:
        items = [
            (253, _UINT32, end_t), *extra_head,
            (2, _UINT32, start_t), (7, _UINT32, ms), (8, _UINT32, ms), (9, _UINT32, _scaled(distance, 100)),
            (11, _UINT16, calories),
            (speed_ids[0], _UINT32, avg_speed), (speed_ids[1], _UINT32, max_speed),
            (ids["avg_hr"], _UINT8, avg_hr), (ids["avg_hr"] + 1, _UINT8, max_hr),
            (ids["avg_hr"] + 2, _UINT8, avg_cad), (ids["avg_hr"] + 3, _UINT8, max_cad),
            (ids["avg_hr"] + 4, _UINT16, avg_pow), (ids["avg_hr"] + 5, _UINT16, max_pow),
            (ids["avg_hr"] + 6, _UINT16, ascent), (ids["avg_hr"] + 7, _UINT16, descent),
            *tail,
        ]
        w.message(local, num, items)

    summary(5, 19, [(0, _ENUM, 9), (1, _ENUM, 1)], {"avg_hr": 15}, (110, 111), [(25, _ENUM, 2)])
    summary(6, 18, [(0, _ENUM, 8), (1, _ENUM, 1)], {"avg_hr": 16}, (124, 125),
            [(5, _ENUM, 2), (6, _ENUM, 47), (25, _UINT16, 0), (26, _UINT16, 1)])

    # activity
    w.message(7, 34, [
        (253, _UINT32, end_t), (0, _UINT32, ms), (1, _UINT16, 1), (2, _ENUM, 0),
        (3, _ENUM, 26), (4, _ENUM, 1), (5, _UINT32, end_t),
    ])

    body = bytes(w.buf)
    head = struct.pack("<BBHI4s", 14, 0x20, 2140, len(body), b".FIT")
    head += struct.pack("<H", fit_crc(head))
    out = head + body
    return out + struct.pack("<H", fit_crc(out))


__all__ = ["to_fit", "fit_crc"]
