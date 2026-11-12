"""Protokoll der ESP32-Einsendungen: Ereignisse und zuletzt gesehene Geräte."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from .db import Database

KEEP_EVENTS = 1000
DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SyncLog:
    def __init__(self, db: Database):
        self.db = db
        self._since_prune = 0

    def event(self, kind: str, ip: str | None, device_id: str | None = None, *, path=None, size=None, bytes_=None, status=None, detail=None) -> None:
        ts = now_iso()
        self.db.execute(
            "INSERT INTO sync_events(ts, client_ip, device_id, kind, path, size, bytes, status, detail) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, ip, device_id, kind, path, size, bytes_, status, detail),
        )
        if device_id:
            self.touch(device_id, ip, ts)
        self._since_prune += 1
        if self._since_prune >= 50:
            self.prune()

    def prune(self) -> None:
        self._since_prune = 0
        self.db.execute("DELETE FROM sync_events WHERE id NOT IN (SELECT id FROM sync_events ORDER BY id DESC LIMIT ?)", (KEEP_EVENTS,))

    def touch(self, device_id: str, ip: str | None, ts: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO devices(device_id, last_seen, last_ip) VALUES (?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen, last_ip = COALESCE(excluded.last_ip, last_ip)",
            (device_id, ts or now_iso(), ip),
        )

    def report(self, body: dict, ip: str | None) -> str:
        """Speichert einen Gerätebericht; gibt die Serverzeit zurück."""
        ts = now_iso()
        did = body["device_id"]
        sync = body.get("sync") if body["event"] == "sync" else None
        self.db.execute(
            "INSERT INTO devices(device_id, last_seen, last_ip, firmware, last_report_json) VALUES (?,?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen, last_ip = excluded.last_ip, "
            "firmware = COALESCE(excluded.firmware, firmware), last_report_json = excluded.last_report_json",
            (did, ts, ip, body.get("firmware"), json.dumps(body, ensure_ascii=False)),
        )
        if sync is not None:
            self.db.execute("UPDATE devices SET last_sync_json = ?, last_sync_at = ? WHERE device_id = ?", (json.dumps(sync, ensure_ascii=False), ts, did))
        detail = None
        if sync:
            detail = f"{sync.get('result') or '?'}: neu {sync.get('new', 0)}, übersprungen {sync.get('skipped', 0)}, fehlerhaft {sync.get('failed', 0)}"
            if sync.get("error"):
                detail += f" – {sync['error']}"
        elif body.get("drive"):
            drive = {"present": "erkannt", "absent": "nicht erkannt", "error": "Fehler"}.get(body["drive"], body["drive"])
            detail = f"Laufwerk: {drive}"
        ota = body.get("ota") if body["event"] == "ota" else None
        if ota:
            result = {"started": "gestartet", "ok": "erfolgreich", "failed": "fehlgeschlagen", "rolled_back": "zurückgesetzt"}.get(ota.get("result"), ota.get("result") or "?")
            detail = f"Update {ota.get('from') or '?'} → {ota.get('to') or '?'}: {result}"
            if ota.get("error"):
                detail += f" – {ota['error']}"
            self.event("ota", ip, did, detail=detail)
            return ts
        self.event("report" if sync is not None else "heartbeat", ip, did, bytes_=(sync or {}).get("bytes"), detail=detail)
        return ts

    def snapshot(self, limit: int = 200) -> dict:
        devices = []
        for r in self.db.query("SELECT * FROM devices ORDER BY last_seen DESC"):
            d = dict(r)
            d["last_report"] = json.loads(d.pop("last_report_json") or "null")
            d["last_sync"] = json.loads(d.pop("last_sync_json") or "null")
            devices.append(d)
        events = [dict(r) for r in self.db.query("SELECT * FROM sync_events ORDER BY id DESC LIMIT ?", (limit,))]
        return {"devices": devices, "events": events}
