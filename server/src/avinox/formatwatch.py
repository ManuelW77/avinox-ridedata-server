"""Format-Wächter: erkennt bei neuen Display-Dateien Abweichungen vom bekannten Datenformat.

Prüft nur Struktur (Versionen, Feldnummern, Ereignis- und Feldnamen), nie Werte.
Damit tauchen z. B. nach einem DJI-Firmware-Update neue Felder auf, ohne dass
Koordinaten oder Seriennummern gespeichert werden. Meldungen des Rads (HMS) werden mitgeführt.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from .hms import IGNORED_CODES, parse_hms
from .protobuf import ProtobufError, decode_message
from .ride_rec import FRAME_HEAD, FRAME_TAIL, FRAME_TYPE_SAMPLE, HEADER_SIZE, KNOWN_FIELDS, parse_header

KNOWN_HEADER_VERSION = 8


def _fs(*names: str) -> frozenset[str]:
    return frozenset(names)


def _series(prefix: str, *suffixes: str) -> list[str]:
    return [f"{prefix}{s}" for s in suffixes]


# Bekannte Statistik-Ereignisse und ihre Feldnamen (ohne event_id und time_now).
_BURIED_BASELINE: dict[str, frozenset[str]] = {
    "Firmware_version": _fs("Bat", "Ble", "Motor", "Package", "Screen"),
    "wireless_controller_usage": _fs("L1_long", "L1_single", "L2_long", "L2_single", "R1_long", "R1_single", "R2_long", "R2_single"),
    "single_power": _fs("distance", "duration"),
    "mos_ntc_temperture": _fs("limit", "temperature_less_60", *_series("temperature_", "60", "70", "80", "90", "100", "110", "120", "130")),
    "battery_temperture": _fs("limit", "temperature_less_30", "temperature_more_60", "temperature_30", "temperature_40", "temperature_50"),
    "bike_pitch_attitude": _fs(
        *_series("slope_less_", "10", "20", "30", "40", "negative_0", "negative_10", "negative_20", "negative_30", "negative_40"), "slope_more_40"
    ),
    "bike_horizontal_attitude": _fs(*_series("roll_less_", "10", "20", "30", "40"), "roll_more_40"),
    "battery_usage": _fs(
        "end_battery", "end_temperture", "max_cell_voltage", "max_current", "max_delta_cell_voltage", "max_temperture",
        "min_cell_voltage", "min_total_voltage", "ptc_pf", "start_battery", "start_temperture", "temperture",
    ),
    "sim_usage": _fs("result"),
    "assist_mode_order": _fs("auto_order", "eco_order", "trail_order", "turbo_order"),
    "single_ride": _fs("avg_speed", "battery", "distance", "duration", "elev_gain", "elev_start"),
    "rider_cadence": _fs("average", "max"),
    "rider_torque": _fs("average", "max"),
    "rider_heart_rate": _fs("average", "max"),
    "motor_torque": _fs("avg_speed", "distance", "duration", "torque_less_50", *_series("torque_", "50", "60", "70", "80", "90", "100", "110")),
    "ride_power": _fs("avg_speed", "distance", "duration", "power_less_500", *_series("power_", "500", "600", "700", "800", "900")),
    "boost_mode": _fs("avg_speed", "boost_100", "boost_110", "boost_duration", "distance", "duration"),
    "gear_shift": _fs("avg_speed", "distance", "duration", *_series("gear_shift_", *map(str, range(1, 13)))),
    "assist_mode_usage": _fs(
        "auto_duration", "avg_speed", "change_number", "distance", "duration", "eco_duration", "trail_duration", "turbo_duration"
    ),
    "wireless_controller_customize": _fs("control", "method", "model"),
    "ass_mode_para": _fs(
        "ass_coeff", "ass_constant", "ass_max", "ass_min", "ass_start", "duration", "max_power", "max_torque", "mode", "overrun", "status"
    ),
    "battery_circle": _fs("number"),
    "battery_life_data": _fs("total_trip", "total_climb"),
}
# Schlüssel in general_msg (Kopfzeile je Einschaltvorgang)
_GENERAL_KEYS = frozenset({"Product_type", "boot_times"})
_ENVELOPE = frozenset({"event_id", "time_now"})


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str  # header_version | frame_type | ride_field | buried_event | buried_key | hms
    key: str
    detail: str


def _type_name(value: object) -> str:
    return "bytes" if isinstance(value, (bytes, bytearray)) else type(value).__name__


def _check_ride(data: bytes) -> list[Finding]:
    out: list[Finding] = []
    if len(data) < HEADER_SIZE:
        return out
    try:
        version = parse_header(data).format_version
        if version != KNOWN_HEADER_VERSION:
            out.append(Finding("header_version", str(version), f"{version}"))
    except Exception:  # defekter Header ist Sache des Parsers
        pass
    types: dict[int, int] = {}
    fields: dict[int, list] = {}  # Feldnummer -> [Anzahl, Typname]
    pos, size = HEADER_SIZE, len(data)
    while pos + FRAME_HEAD <= size:
        frame_type, length = struct.unpack_from("<HH", data, pos)
        end = pos + FRAME_HEAD + length
        if end + FRAME_TAIL > size:
            break
        if frame_type != FRAME_TYPE_SAMPLE:
            types[frame_type] = types.get(frame_type, 0) + 1
        else:
            try:
                msg = decode_message(data[pos + FRAME_HEAD : end])
            except ProtobufError:
                msg = {}
            for num, val in msg.items():
                if num not in KNOWN_FIELDS:
                    fields.setdefault(num, [0, _type_name(val)])[0] += 1
        pos = end + FRAME_TAIL
    for ft, n in sorted(types.items()):
        out.append(Finding("frame_type", f"0x{ft:04X}", str(n)))
    for num, (n, tname) in sorted(fields.items()):
        out.append(Finding("ride_field", str(num), f"{n}|{tname}"))
    return out


def _check_buried(data: bytes) -> list[Finding]:
    seen: dict[str, set[str]] = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if "general_msg" in obj:
            inner = obj["general_msg"] if isinstance(obj["general_msg"], dict) else {}
            seen.setdefault("general_msg", set()).update(set(inner) - _GENERAL_KEYS)
        elif "event_id" in obj:
            seen.setdefault(str(obj["event_id"]), set()).update(set(obj) - _ENVELOPE)
    out: list[Finding] = []
    for event, keys in sorted(seen.items()):
        known = _GENERAL_KEYS & keys if event == "general_msg" else _BURIED_BASELINE.get(event)
        if event == "general_msg":
            out += [Finding("buried_key", f"general_msg.{k}", "") for k in sorted(keys)]
        elif known is None:
            out.append(Finding("buried_event", event, str(len(keys))))
        else:
            out += [Finding("buried_key", f"{event}.{k}", "") for k in sorted(keys - known)]
    return out


def _check_hms(data: bytes) -> list[Finding]:
    out = []
    for e in parse_hms(data):
        if e.code is None or e.code in IGNORED_CODES:
            continue
        when = e.time.isoformat() if e.time else "?"
        out.append(Finding("hms", f"{e.code}@{when}", f"{e.code}|0x{e.code:04X}"))
    return out


def check(kind: str, path: str, data: bytes) -> list[Finding]:
    """Findet Abweichungen vom bekannten Format. ``path`` dient nur dem Aufrufer zur Zuordnung."""
    if kind == "ride":
        return _check_ride(data)
    if kind == "buried":
        return _check_buried(data)
    if kind == "hms":
        return _check_hms(data)
    return []
