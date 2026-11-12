"""Parser für Fahrtaufzeichnungen ``ride_rec_<id>.proto`` vom Avinox-Display.

Aufbau der Datei (little-endian), siehe ERKENNTNISSE.md:

* Header, 251 Byte, beginnt mit Magic ``0xA5A5A5A5``
* danach Frames: ``uint16 typ (0x02EB) | uint16 länge | Protobuf (länge Byte) | 2 Byte Prüfsumme``
  Ein Frame ist ein Sample, etwa eins pro Sekunde.

Die Feldzuordnung stammt aus Community-Reverse-Engineering
(``lucad87/avinox-setup-app``, MIT) und eigenen Prüfungen. Sie ist nicht
offiziell; Werte vor dem Vertrauen gegen die Avinox-App plausibilisieren.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .protobuf import ProtobufError, decode_message, to_signed64

MAGIC = 0xA5A5A5A5
HEADER_SIZE = 251
FRAME_TYPE_SAMPLE = 0x02EB
FRAME_HEAD = 4
FRAME_TAIL = 2  # Prüfsumme, Verfahren noch unbekannt; wird nicht geprüft

# Zeitwerte unterhalb dieser Grenze sind Sekunden seit Geräte-Start, keine Unix-Zeit.
# Das kommt bei Fahrten vor, bevor das Display zum ersten Mal die Uhrzeit hatte.
_UNIX_MIN = 1_000_000_000

# Protobuf-Feldnummern der Sample-Nachricht, die hier in Kanäle übersetzt werden.
KNOWN_FIELDS = frozenset(
    {6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 22, 23, 31, 32, 36, 37, 38, 39, 40, 41, 42, 51, 52, 71, 72, 73, 75}
)


class RideRecError(ValueError):
    """Datei ist keine gültige Fahrtaufzeichnung."""


@dataclass(slots=True)
class HeaderStats:
    """Vom Display berechnete Kennzahlen, Offset 161–236 (19 × float32).

    Das sind die Werte, die die Avinox-App anzeigt (geprüft an einer Fahrt,
    Gesamt = Fahrer + Motor geht in allen Testfahrten exakt auf).
    """

    avg_gradient_pct: float
    max_gradient_pct: float
    avg_speed_kmh: float
    max_speed_kmh: float
    avg_total_power_w: float
    max_total_power_w: float
    avg_total_torque_nm: float
    max_total_torque_nm: float
    avg_motor_power_w: float
    max_motor_power_w: float
    avg_motor_torque_nm: float
    max_motor_torque_nm: float
    avg_rider_power_w: float
    max_rider_power_w: float
    avg_rider_torque_nm: float
    max_rider_torque_nm: float
    avg_cadence_rpm: float
    max_cadence_rpm: float
    calories_kcal: float
    """Läuft bei kurz aufeinanderfolgenden Fahrten teils weiter statt neu zu beginnen."""

    @classmethod
    def from_header(cls, data: bytes) -> HeaderStats:
        values = list(struct.unpack_from("<19f", data, 161))
        values[18] /= 1000
        return cls(*values)


@dataclass(slots=True)
class Header:
    ride_id: int
    format_version: int
    start: int
    """Start: Unix-Sekunden oder Sekunden seit Geräte-Start (siehe ``has_wallclock``)."""
    end: int
    distance_m: int
    sample_count: int
    """Anzahl Samples laut Header; die Frames in der Datei sind maßgeblich."""
    serial: str
    """Seriennummer des Antriebs. Privat: nie ausgeben oder speichern, wo es öffentlich wird."""
    ascent_m: float
    """Offset 22, Höhengewinn wie in der App (geprüft). Kleiner als aus den Höhen-Samples."""
    descent_m: float
    """Offset 26, Höhenabnahme wie in der App (geprüft)."""
    owner_id: str
    """Offset 128, ID mit Längenbyte; entspricht dem Unterordner unter ``ride_rec/``
    (vermutlich Konto). Leer bei Fahrten vor der Kopplung. Privat."""
    preview: list[tuple[float, float]]
    """Offset 30–109: bis zu 10 Punkte (Breite, Länge) als int32 × 1e7 für die Kartenvorschau."""
    stats: HeaderStats

    @property
    def has_wallclock(self) -> bool:
        return self.start >= _UNIX_MIN

    @property
    def duration_s(self) -> int:
        return self.end - self.start

    @property
    def start_time(self) -> datetime | None:
        return datetime.fromtimestamp(self.start, UTC) if self.has_wallclock else None


@dataclass(slots=True)
class Sample:
    seq: int | None
    """Laufender Zähler (Feld 6), zählt auch Lücken."""
    timestamp: int | None
    """Feld 72: Unix-Sekunden oder Sekunden seit Geräte-Start, wie der Header."""
    speed_kmh: float
    assist_level: int
    cadence_rpm: float
    motor_torque_nm: float
    rider_torque_nm: float
    total_torque_nm: float
    rider_power_w: float
    motor_power_w: float
    total_power_w: float
    gear: int
    distance_m: int
    latitude: float | None
    longitude: float | None
    imu: tuple[float | None, float | None, float | None]
    altitude_m: float | None
    gradient_pct: float | None
    temperature_c: float | None
    pressure_kpa: float | None
    heart_rate_bpm: int | None
    calories_kcal: float | None
    """Feld 52, Kalorien bisher (÷1000). Letzter Wert = Header ``stats.calories_kcal``."""
    battery_pct: int | None
    odometer_m: int | None
    event: int
    extra: dict[int, int | bytes] = field(default_factory=dict)
    """Felder ohne bekannte Bedeutung, für weiteres Reverse-Engineering."""


@dataclass(slots=True)
class Ride:
    header: Header
    samples: list[Sample]
    warnings: list[str] = field(default_factory=list)


def _f32(value: int | bytes | None) -> float | None:
    if isinstance(value, bytes) and len(value) == 4:
        return struct.unpack("<f", value)[0]
    return None


def _f64(value: int | bytes | None) -> float | None:
    if isinstance(value, bytes) and len(value) == 8:
        return struct.unpack("<d", value)[0]
    return None


def _int(value: int | bytes | None) -> int | None:
    return value if isinstance(value, int) else None


def _scaled(value: int | bytes | None, divisor: float) -> float:
    """Fehlendes Feld = 0 (Protobuf lässt Nullwerte weg)."""
    return value / divisor if isinstance(value, int) else 0.0


def _scaled_signed(value: int | bytes | None, divisor: float) -> float | None:
    """Fehlendes Feld = unbekannt. Eine fehlende Höhe als 0 m würde Höhenmeter erfinden."""
    return to_signed64(value) / divisor if isinstance(value, int) else None


def _coord(value: int | bytes | None, limit: float) -> float | None:
    coord = _f64(value)
    if coord is None or coord != coord or abs(coord) > limit or coord == 0.0:
        return None
    return coord


def sample_from_fields(f: dict[int, int | bytes]) -> Sample:
    temp = _scaled_signed(f.get(41), 100)
    if temp is not None and temp <= -999:
        temp = None
    pressure = _scaled(f.get(42), 100) or None  # Rohwert z. B. 10149 -> 101,49 kPa
    energy = f.get(52)
    distance = _int(f.get(23)) or 0
    return Sample(
        seq=_int(f.get(6)),
        timestamp=_int(f.get(72)),
        speed_kmh=_scaled(f.get(7), 100),
        assist_level=_int(f.get(8)) or 0,
        cadence_rpm=_scaled(f.get(9), 100),
        motor_torque_nm=_scaled(f.get(10), 100),
        rider_torque_nm=_scaled(f.get(11), 100),
        total_torque_nm=_scaled(f.get(12), 100),
        rider_power_w=_scaled(f.get(13), 100),
        motor_power_w=_scaled(f.get(14), 100),
        total_power_w=_scaled(f.get(15), 100),
        gear=_int(f.get(22)) or 0,
        distance_m=distance,
        latitude=_coord(f.get(31), 90),
        longitude=_coord(f.get(32), 180),
        imu=(_f32(f.get(36)), _f32(f.get(37)), _f32(f.get(38))),
        altitude_m=_scaled_signed(f.get(39), 100),
        gradient_pct=_scaled_signed(f.get(40), 100),
        temperature_c=temp,
        pressure_kpa=pressure,
        heart_rate_bpm=_int(f.get(51)) or None,
        calories_kcal=energy / 1000 if isinstance(energy, int) else None,
        battery_pct=_int(f.get(71)),
        odometer_m=_int(f.get(73)),
        event=_int(f.get(75)) or 0,
        extra={k: v for k, v in f.items() if k not in KNOWN_FIELDS},
    )


def parse_header(data: bytes) -> Header:
    if len(data) < HEADER_SIZE:
        raise RideRecError(f"Datei zu kurz für Header ({len(data)} < {HEADER_SIZE} Byte)")
    if struct.unpack_from("<I", data, 0)[0] != MAGIC:
        raise RideRecError("Kein Avinox-Fahrtheader (Magic fehlt)")
    version, ride_id, start, end, distance = struct.unpack_from("<HIIII", data, 4)
    ascent, descent = struct.unpack_from("<ff", data, 22)
    serial = data[110:128].split(b"\0", 1)[0].decode("ascii", "replace")
    owner_len = min(data[128], 31)
    owner_id = data[129 : 129 + owner_len].decode("ascii", "replace")
    preview = []
    for i in range(10):
        lat, lon = struct.unpack_from("<ii", data, 30 + 8 * i)
        if lat or lon:
            preview.append((lat / 1e7, lon / 1e7))
    (count,) = struct.unpack_from("<I", data, 245)
    return Header(
        ride_id=ride_id,
        format_version=version,
        start=start,
        end=end,
        distance_m=distance,
        sample_count=count,
        serial=serial,
        ascent_m=ascent,
        descent_m=descent,
        owner_id=owner_id,
        preview=preview,
        stats=HeaderStats.from_header(data),
    )


def parse_ride(data: bytes) -> Ride:
    """Liest eine komplette Fahrt. Defekte Frames werden übersprungen und in ``warnings`` vermerkt."""
    header = parse_header(data)
    ride = Ride(header=header, samples=[])
    pos = HEADER_SIZE
    size = len(data)
    while pos < size:
        if pos + FRAME_HEAD > size:
            ride.warnings.append(f"Rest von {size - pos} Byte am Dateiende ignoriert")
            break
        frame_type, length = struct.unpack_from("<HH", data, pos)
        body_end = pos + FRAME_HEAD + length
        if body_end + FRAME_TAIL > size:
            ride.warnings.append(f"Frame bei Byte {pos} abgeschnitten")
            break
        if frame_type == FRAME_TYPE_SAMPLE:
            try:
                ride.samples.append(sample_from_fields(decode_message(data[pos + FRAME_HEAD : body_end])))
            except ProtobufError as exc:
                ride.warnings.append(f"Frame bei Byte {pos} unlesbar: {exc}")
        else:
            ride.warnings.append(f"Unbekannter Frame-Typ 0x{frame_type:04X} bei Byte {pos}")
        pos = body_end + FRAME_TAIL
    if len(ride.samples) < header.sample_count:
        ride.warnings.append(f"Header nennt {header.sample_count} Samples, gelesen {len(ride.samples)}")
    return ride


def parse_file(path: str | Path) -> Ride:
    return parse_ride(Path(path).read_bytes())
