"""Gesundheits-/Fehlermeldungen ``hms_rec/hms_current_storage.proto`` (DJI „HMS“).

Aufbau wie die Fahrtdateien, nur kürzer: Magic ``0xA5A5A5A5``, uint32 Version,
Seriennummer (Offset 8, 18 Byte), 4 Byte unbekannt, danach Frames
``uint16 typ (0x01EB) | uint16 länge | Protobuf | 2 Byte Prüfsumme``.
Protobuf: Feld 1 = Meldungscode, Feld 4 = Unix-Zeit. Bedeutung der Codes unbekannt.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .protobuf import ProtobufError, decode_message
from .ride_rec import MAGIC

HEADER_SIZE = 32
FRAME_TYPE_HMS = 0x01EB


@dataclass(slots=True)
class HmsEntry:
    code: int | None
    time: datetime | None
    extra: dict[int, int | bytes] = field(default_factory=dict)


def parse_hms(data: bytes) -> list[HmsEntry]:
    if len(data) < HEADER_SIZE or struct.unpack_from("<I", data, 0)[0] != MAGIC:
        return []
    entries: list[HmsEntry] = []
    pos = HEADER_SIZE
    while pos + 6 <= len(data):
        frame_type, length = struct.unpack_from("<HH", data, pos)
        end = pos + 4 + length
        if end + 2 > len(data):
            break
        if frame_type == FRAME_TYPE_HMS:
            try:
                f = decode_message(data[pos + 4 : end])
            except ProtobufError:
                f = {}
            ts = f.get(4)
            entries.append(
                HmsEntry(
                    code=f.get(1) if isinstance(f.get(1), int) else None,
                    time=datetime.fromtimestamp(ts, UTC) if isinstance(ts, int) and ts > 1_000_000_000 else None,
                    extra={k: v for k, v in f.items() if k not in (1, 4)},
                )
            )
        pos = end + 2
    return entries


def parse_hms_file(path: str | Path) -> list[HmsEntry]:
    return parse_hms(Path(path).read_bytes())
