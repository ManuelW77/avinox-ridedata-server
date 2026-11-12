"""Minimaler Protobuf-Wire-Decoder (ohne Schema).

Die Fahrtdateien enthalten pro Sample eine Protobuf-Nachricht ohne bekanntes
.proto-Schema. Für die Auswertung reicht es, die Felder roh zu lesen:
Feldnummer -> Wert (int für varint, bytes für fixed32/fixed64/length-delimited).
"""

from __future__ import annotations


class ProtobufError(ValueError):
    """Nachricht ist kein gültiges Protobuf (abgeschnitten oder unbekannter Wire-Typ)."""


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ProtobufError("varint abgeschnitten")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ProtobufError("varint zu lang")


def decode_message(buf: bytes) -> dict[int, int | bytes]:
    """Dekodiert eine Nachricht flach. Bei doppelten Feldern gewinnt das letzte."""
    fields: dict[int, int | bytes] = {}
    pos = 0
    end = len(buf)
    while pos < end:
        tag, pos = read_varint(buf, pos)
        number, wire = tag >> 3, tag & 0x7
        if number == 0:
            raise ProtobufError("Feldnummer 0")
        if wire == 0:
            value, pos = read_varint(buf, pos)
        elif wire == 1:
            value, pos = buf[pos : pos + 8], pos + 8
        elif wire == 5:
            value, pos = buf[pos : pos + 4], pos + 4
        elif wire == 2:
            length, pos = read_varint(buf, pos)
            value, pos = buf[pos : pos + length], pos + length
        else:
            raise ProtobufError(f"Wire-Typ {wire} nicht unterstützt")
        if pos > end:
            raise ProtobufError("Feld abgeschnitten")
        fields[number] = value
    return fields


def to_signed64(value: int) -> int:
    """varint als int64 (Zweierkomplement) lesen."""
    return value - (1 << 64) if value >= 1 << 63 else value
