"""Einstellungen aus Umgebungsvariablen.

| Variable | Standard | Bedeutung |
|---|---|---|
| ``AVINOX_DATA_DIR`` | ``./data`` | Rohdateien, Datenbank, Upload-Token |
| ``AVINOX_UPLOAD_TOKEN`` | zufällig, in ``<data>/upload_token`` | Bearer-Token für Uploads (ESP32) |
| ``AVINOX_WEB_USER`` / ``AVINOX_WEB_PASSWORD`` | leer | Start-Admin, der einzige Weg zum ersten Administrator (die Weboberfläche legt keinen an; weitere Benutzer in den Einstellungen): wird beim Start als Administrator angelegt, falls es den Benutzer noch nicht gibt (ein vorhandener wird nie überschrieben, spätere Änderungen in der Datenbank bleiben). Schaltet den Login ein und fest (Seite per Formular, API zusätzlich per Basic Auth). Sonst ist die Anmeldung standardmäßig aus; nach dem ersten Start-Admin lässt sie sich in den Einstellungen wieder ein- und ausschalten |
| ``AVINOX_OIDC_ISSUER`` / ``AVINOX_OIDC_CLIENT_ID`` | leer | Anmeldung über einen OpenID-Connect-Anbieter (Authorization Code mit PKCE); Redirect-URI ``<Adresse>/auth/oidc/callback``; hat Vorrang vor den Einstellungen (dort nur lesbar) |
| ``AVINOX_OIDC_CLIENT_SECRET`` | leer | Client-Secret (leer = öffentlicher Client) |
| ``AVINOX_OIDC_NAME`` | ``SSO`` | Name auf dem Knopf „Mit … anmelden“ |
| ``AVINOX_OIDC_SCOPES`` | ``openid email profile`` | Angeforderte Scopes (für Gruppen ggf. ``groups`` ergänzen) |
| ``AVINOX_OIDC_ALLOWED`` | leer | Kommagetrennt: E-Mail-Adressen oder Gruppen (Claim ``groups``), die sich anmelden dürfen; leer = jeder, den der Anbieter durchlässt |
| ``AVINOX_OIDC_ADMIN`` | leer | Gleiche Schreibweise: wer Administrator wird; leer = alle OIDC-Benutzer sind Admin (privater Einzelserver) |
| ``AVINOX_PUBLIC_URL`` | leer | Adresse des Servers für den ESP32, z. B. ``http://192.168.1.18:8000`` (sonst aus der Browser-Anfrage) |
| ``AVINOX_TZ`` | ``Europe/Berlin`` | Zeitzone für die Anzeige |
| ``AVINOX_MAP_STYLE`` | OpenFreeMap „liberty“ | Style-URL (MapLibre, Vektorkarte) für die Fahrtkarte und die 3D-Ansicht |
| ``AVINOX_GEOCODE_URL`` | OpenStreetMap Nominatim | Dienst für Ortsnamen der Fahrten (erst nach Einschalten in den Einstellungen aktiv, Koordinaten auf ca. 100 m gerundet); ``off`` schaltet ihn ganz ab |
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
    map_style: str
    max_upload_bytes: int
    geocode_url: str | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_name: str = "SSO"
    oidc_scopes: str = "openid email profile"
    oidc_allowed: str = ""
    oidc_admin: str = ""

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


def _geocode_url() -> str | None:
    value = os.environ.get("AVINOX_GEOCODE_URL", "").strip() or "https://nominatim.openstreetmap.org"
    return None if value.lower() == "off" else value.rstrip("/")


def load_settings(data_dir: str | Path | None = None) -> Settings:
    base = Path(data_dir or os.environ.get("AVINOX_DATA_DIR", "./data")).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=base,
        upload_token=_token(base),
        web_user=os.environ.get("AVINOX_WEB_USER") or None,
        web_password=os.environ.get("AVINOX_WEB_PASSWORD") or None,
        public_url=(",".join(u.strip().rstrip("/") for u in os.environ.get("AVINOX_PUBLIC_URL", "").split(",") if u.strip()) or None),
        tz=ZoneInfo(os.environ.get("AVINOX_TZ", "Europe/Berlin")),
        map_style=os.environ.get("AVINOX_MAP_STYLE") or "https://tiles.openfreemap.org/styles/liberty",
        max_upload_bytes=int(os.environ.get("AVINOX_MAX_UPLOAD_MB", "64")) * 1024 * 1024,
        geocode_url=_geocode_url(),
        oidc_issuer=(os.environ.get("AVINOX_OIDC_ISSUER", "").strip().rstrip("/") or None),
        oidc_client_id=os.environ.get("AVINOX_OIDC_CLIENT_ID", "").strip() or None,
        oidc_client_secret=os.environ.get("AVINOX_OIDC_CLIENT_SECRET", "").strip() or None,
        oidc_name=os.environ.get("AVINOX_OIDC_NAME", "").strip() or "SSO",
        oidc_scopes=os.environ.get("AVINOX_OIDC_SCOPES", "").strip() or "openid email profile",
        oidc_allowed=os.environ.get("AVINOX_OIDC_ALLOWED", ""),
        oidc_admin=os.environ.get("AVINOX_OIDC_ADMIN", ""),
    )
