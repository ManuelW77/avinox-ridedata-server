"""ESP-IDF-App-Image lesen (Version und Projektname aus ``esp_app_desc_t``)."""

from __future__ import annotations

import struct

MAX_FIRMWARE_BYTES = 2 * 1024 * 1024
IMAGE_MAGIC = 0xE9
DESC_MAGIC = 0xABCD5432
DESC_OFFSET = 0x20
DESC_SIZE = 4 + 4 + 8 + 32 + 32 + 16 + 16 + 32 + 32


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def read_app_info(data: bytes) -> dict:
    """Liest Version, Projekt und Bauzeit; ``ValueError`` bei ungültigem Image."""
    if len(data) > MAX_FIRMWARE_BYTES:
        raise ValueError("Image größer als 2 MB")
    if len(data) < DESC_OFFSET + DESC_SIZE:
        raise ValueError("Image zu kurz")
    if data[0] != IMAGE_MAGIC:
        raise ValueError("Kein ESP-IDF-Image (Magic-Byte 0xE9 fehlt)")
    magic, _secure, _res, version, project, time_, date_, idf, _sha = struct.unpack_from("<II8s32s32s16s16s32s32s", data, DESC_OFFSET)
    if magic != DESC_MAGIC:
        raise ValueError("App-Beschreibung (esp_app_desc_t) nicht gefunden")
    info = {"version": _text(version), "project_name": _text(project), "time": _text(time_), "date": _text(date_), "idf_ver": _text(idf)}
    if not info["version"] or not info["project_name"]:
        raise ValueError("Version oder Projektname fehlt im Image")
    return info
