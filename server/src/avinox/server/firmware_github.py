"""Neueste ESP-Firmware aus den GitHub-Releases holen (Tags ``fw-vX.Y.Z``)."""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import urlparse

import httpx2

from .firmware import MAX_FIRMWARE_BYTES, read_app_info

log = logging.getLogger("avinox.server")

REPO = "ManuelW77/avinox-ridedata-server"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
ASSET_NAME = "avinox-sync.bin"
TAG_RE = re.compile(r"^fw-v(\d+(?:\.\d+)*)$")
ALLOWED_HOSTS = {"github.com", "api.github.com"}
TIMEOUT = 15.0
MAX_REDIRECTS = 5
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "avinox-ridedata-server"}

# Test-Haken: Transport für httpx2 (z. B. MockTransport); None = echtes Netz
TRANSPORT: httpx2.BaseTransport | None = None


class GithubError(Exception):
    """Verständliche Fehlermeldung (Deutsch) für die Oberfläche."""


def _client() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT, headers=HEADERS, transport=TRANSPORT)


def _host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host in ALLOWED_HOSTS or host.endswith(".githubusercontent.com"))


def _version_key(tag: str) -> tuple[int, ...]:
    m = TAG_RE.match(tag)
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def fetch_latest_fw_release(client: httpx2.Client | None = None) -> dict | None:
    """Höchstes veröffentlichtes ``fw-v*``-Release (ohne Entwürfe/Vorabversionen) oder None."""
    own = client is None
    client = client or _client()
    try:
        r = client.get(RELEASES_URL)
        if r.status_code != 200:
            raise GithubError(f"GitHub antwortete mit HTTP {r.status_code}.")
        releases = r.json()
    except (httpx2.HTTPError, ValueError) as exc:
        raise GithubError(f"GitHub ist nicht erreichbar ({type(exc).__name__}).") from exc
    finally:
        if own:
            client.close()
    if not isinstance(releases, list):
        raise GithubError("Unerwartete Antwort von GitHub.")
    best = None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name", ""))
        if _version_key(tag) and (best is None or _version_key(tag) > _version_key(str(best["tag_name"]))):
            best = rel
    return best


def _download(client: httpx2.Client, url: str) -> bytes:
    for _ in range(MAX_REDIRECTS + 1):
        if not _host_allowed(url):
            raise GithubError("Der Download-Host ist nicht erlaubt.")
        with client.stream("GET", url, follow_redirects=False) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise GithubError("Weiterleitung ohne Ziel.")
                url = str(resp.url.join(location))
                continue
            if resp.status_code != 200:
                raise GithubError(f"Download fehlgeschlagen (HTTP {resp.status_code}).")
            data = bytearray()
            for chunk in resp.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_FIRMWARE_BYTES:
                    raise GithubError("Das Image ist größer als 2 MB.")
            return bytes(data)
    raise GithubError("Zu viele Weiterleitungen.")


def import_latest_release(store) -> dict:
    """Lädt die neueste Firmware und setzt sie als aktuell; wirft nie, liefert immer ein Ergebnis."""
    result = {"release": None, "version": None, "status": "failed", "message": ""}
    try:
        with _client() as client:
            rel = fetch_latest_fw_release(client)
            if rel is None:
                return {**result, "status": "none", "message": "Auf GitHub gibt es noch keine Firmware-Version."}
            result["release"] = str(rel["tag_name"])
            asset = next((a for a in rel.get("assets") or [] if a.get("name") == ASSET_NAME), None)
            if asset is None:
                raise GithubError(f"Im Release {result['release']} fehlt die Datei {ASSET_NAME}.")
            if int(asset.get("size") or 0) > MAX_FIRMWARE_BYTES:
                raise GithubError("Das Image ist größer als 2 MB.")
            url = str(asset.get("browser_download_url") or "")
            if not url:
                raise GithubError("Das Release enthält keine Download-Adresse.")
            data = _download(client, url)
        try:
            info = read_app_info(data)
        except ValueError as exc:
            raise GithubError(f"Das Image ist ungültig: {exc}") from exc
        result["version"] = info["version"]
        existing = store.firmware_by_project_version(info["project_name"], info["version"])
        if existing and existing["sha256"] == hashlib.sha256(data).hexdigest():
            return {**result, "status": "exists", "message": f"Version {info['version']} ist schon vorhanden."}
        store.add_firmware(data)
        return {**result, "status": "imported", "message": f"Version {info['version']} geladen und aktiv."}
    except GithubError as exc:
        return {**result, "status": "failed", "message": str(exc)}
    except Exception as exc:  # nie ins UI durchreichen
        log.exception("Firmware-Import von GitHub")
        return {**result, "status": "failed", "message": f"Unerwarteter Fehler ({type(exc).__name__})."}
