"""Einstellungen aus Umgebungsvariablen.

| Variable | Standard | Bedeutung |
|---|---|---|
| ``AVINOX_DATA_DIR`` | ``./data`` | Rohdateien, Datenbank, Upload-Token |
| ``AVINOX_UPLOAD_TOKEN`` | zufällig, in ``<data>/upload_token`` | Bearer-Token für Uploads (ESP32) |
| ``AVINOX_WEB_USER`` / ``AVINOX_WEB_PASSWORD`` | leer | Optionaler Passwortschutz (Basic Auth) für Webseite und Lese-API |
| ``AVINOX_PUBLIC_URL`` | leer | Adresse des Servers für den ESP32, z. B. ``http://192.168.1.18:8000`` (sonst aus der Browser-Anfrage) |
| ``AVINOX_TZ`` | ``Europe/Berlin`` | Zeitzone für die Anzeige |
| ``AVINOX_TILE_URL`` | OpenStreetMap | Kachel-URL für die Karte |
| ``AVINOX_TILE_ATTRIBUTION`` | © OpenStreetMap | Quellenangabe der Karte |
| ``AVINOX_MAX_UPLOAD_MB`` | ``64`` | Größte erlaubte Datei |
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(slots=True)
class Settings:
    data_dir: Path
    upload_token: str
    web_user: str | None
    web_password: str | None
    public_url: str | None
    tz: ZoneInfo
    tile_url: str
    tile_attribution: str
    max_upload_bytes: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "avinox.sqlite"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def incoming_dir(self) -> Path:
        return self.data_dir / "incoming"


def _token(data_dir: Path) -> str:
    env = os.environ.get("AVINOX_UPLOAD_TOKEN", "").strip()
    if env:
        return env
    path = data_dir / "upload_token"
    if path.is_file():
        stored = path.read_text().strip()
        if stored:
            return stored
    token = secrets.token_urlsafe(24)
    path.write_text(token + "\n")
    path.chmod(0o600)
    return token


def load_settings(data_dir: str | Path | None = None) -> Settings:
    base = Path(data_dir or os.environ.get("AVINOX_DATA_DIR", "./data")).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=base,
        upload_token=_token(base),
        web_user=os.environ.get("AVINOX_WEB_USER") or None,
        web_password=os.environ.get("AVINOX_WEB_PASSWORD") or None,
        public_url=(os.environ.get("AVINOX_PUBLIC_URL", "").strip().rstrip("/") or None),
        tz=ZoneInfo(os.environ.get("AVINOX_TZ", "Europe/Berlin")),
        tile_url=os.environ.get("AVINOX_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
        tile_attribution=os.environ.get("AVINOX_TILE_ATTRIBUTION", "© OpenStreetMap-Mitwirkende"),
        max_upload_bytes=int(os.environ.get("AVINOX_MAX_UPLOAD_MB", "64")) * 1024 * 1024,
    )
