"""Hinweis auf neuere Server-Versionen (Tags ``server-vX.Y.Z`` auf GitHub).

Das Ergebnis der Prüfung liegt in der settings-Tabelle; Seitenaufrufe lesen nur diese Werte.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from . import firmware_github as fg

MANUAL_URL = {  # Update-Anleitung im Handbuch, je Oberflächensprache
    "de": "https://avinox.manuelw.de/handbuch.html#updates",
    "en": "https://avinox.manuelw.de/en/manual.html#updates",
}
KEY_ENABLED = "update_check"
KEY_RESULT = "update_result"
_VER_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def is_newer(latest: str, current: str) -> bool:
    """Numerischer Vergleich; unbekannte oder ungültige Versionen gelten nie als neuer."""
    if not _VER_RE.match(latest or "") or not _VER_RE.match(current or ""):
        return False
    return _key(latest) > _key(current)


def enabled(store) -> bool:
    return bool(store.db.get_setting(KEY_ENABLED, True))


def run(store, releases: list) -> dict:
    """Speichert höchste Server-Version, Link und Prüfzeitpunkt."""
    rel = fg.latest_release(releases, fg.SERVER_TAG_RE)
    result = {"latest_version": None, "url": None, "checked_at": datetime.now(timezone.utc).isoformat()}
    if rel:
        result["latest_version"] = str(rel["tag_name"]).removeprefix("server-v")
        url = str(rel.get("html_url") or "")
        result["url"] = url if url.startswith("https://github.com/") else None
    store.db.set_setting(KEY_RESULT, result)
    return result


def state(store, current: str) -> dict:
    """Für die Oberfläche: ``enabled``, ``checked_at``, ``latest``, ``url``, ``newer``."""
    on = enabled(store)
    res = store.db.get_setting(KEY_RESULT, None) or {}
    latest = res.get("latest_version")
    return {
        "enabled": on,
        "checked_at": res.get("checked_at"),
        "latest": latest,
        "url": res.get("url"),
        "newer": bool(on and latest and is_newer(latest, current)),
    }
