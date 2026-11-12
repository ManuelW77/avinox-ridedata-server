"""Tests für das Sync-Protokoll – nur synthetische Daten."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from avinox.server.app import create_app
from avinox.server.config import load_settings
from avinox.server.sync_log import KEEP_EVENTS
from test_server import RIDE_PATH, auth, put, ride_bytes


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("AVINOX_UPLOAD_TOKEN", "AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD", "AVINOX_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path / "data")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def events(client, **kw):
    return client.get("/api/v1/sync/log", params=kw).json()["events"]


def kinds(client):
    return [e["kind"] for e in events(client)]


def test_upload_logged(client, settings):
    data = ride_bytes()
    assert put(client, settings, data).status_code == 200
    assert put(client, settings, data).status_code == 200
    ev = events(client)
    assert [e["kind"] for e in ev] == ["file_known", "file_new"]
    assert ev[1]["path"] == RIDE_PATH and ev[1]["size"] == len(data)
    assert client.get("/api/v1/manifest", headers=auth(settings)).status_code == 200
    assert kinds(client)[0] == "manifest"


def test_conflict_and_error(client, settings):
    data = ride_bytes()
    put(client, settings, data[:10], size=len(data))
    assert put(client, settings, data[20:], offset=20, size=len(data)).status_code == 409
    assert put(client, settings, data, path="evil/x.txt").status_code == 400
    ev = events(client)
    assert (ev[0]["kind"], ev[0]["status"]) == ("upload_chunk_error", 400)
    assert (ev[1]["status"], ev[1]["detail"]) == (409, "Offset korrigiert")


def test_auth_error_without_token(client, settings):
    r = client.put("/api/v1/upload", params={"path": RIDE_PATH, "size": 3}, content=b"abc", headers={"Authorization": "Bearer geheim-falsch"})
    assert r.status_code == 401
    assert events(client)[0]["kind"] == "auth_error"
    con = sqlite3.connect(settings.db_path)
    dump = "\n".join(con.iterdump())
    assert "geheim-falsch" not in dump


def test_report_updates_device(client, settings):
    h = {**auth(settings)}
    hb = {"device_id": "avinox-sync-732D", "event": "heartbeat", "firmware": "1.0.0", "rssi": -55, "drive": "present", "unbekannt": 1}
    r = client.post("/api/v1/device/report", json=hb, headers=h)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["server_time"]
    sync = {**hb, "event": "sync", "sync": {"checked": 4, "new": 3, "skipped": 1, "failed": 0, "bytes": 1000, "duration_ms": 900, "result": "ok", "error": ""}}
    assert client.post("/api/v1/device/report", json=sync, headers=h).status_code == 200
    snap = client.get("/api/v1/sync/log").json()
    dev = snap["devices"][0]
    assert dev["device_id"] == "avinox-sync-732D" and dev["firmware"] == "1.0.0"
    assert dev["last_sync"]["new"] == 3 and dev["last_sync_at"] and "unbekannt" not in dev["last_report"]
    assert [e["kind"] for e in snap["events"]] == ["report", "heartbeat"]


def test_device_header_updates_last_seen(client, settings):
    client.get("/api/v1/manifest", headers={**auth(settings), "X-Avinox-Device": "esp1", "X-Forwarded-For": "10.0.0.5, 1.1.1.1"})
    dev = client.get("/api/v1/sync/log").json()["devices"][0]
    assert dev["device_id"] == "esp1" and dev["last_ip"] == "10.0.0.5"


@pytest.mark.parametrize("body", [
    {"device_id": "böse id", "event": "heartbeat"},
    {"device_id": "x" * 65, "event": "heartbeat"},
    {"event": "heartbeat"},
    {"device_id": "ok", "event": "kaputt"},
    {"device_id": "ok", "event": "heartbeat", "rssi": "laut"},
    {"device_id": "ok", "event": "sync"},
])
def test_report_invalid(client, settings, body):
    assert client.post("/api/v1/device/report", json=body, headers=auth(settings)).status_code == 400


def test_report_needs_token(client):
    assert client.post("/api/v1/device/report", json={"device_id": "a", "event": "heartbeat"}).status_code == 401


def test_sync_page(client, settings):
    r = client.get("/sync")
    assert r.status_code == 200 and "Noch keine Einsendungen" in r.text
    client.post("/api/v1/device/report", json={"device_id": "esp1", "event": "sync", "drive": "absent", "rssi": -80,
                                               "sync": {"new": 1, "result": "warn", "error": "Fehlertext"}}, headers=auth(settings))
    put(client, settings, ride_bytes())
    r = client.get("/sync")
    assert r.status_code == 200 and "esp1" in r.text and "Fehlertext" in r.text and "schwach" in r.text and "Datei neu" in r.text


def test_prune(client, settings):
    log = client.app.state.synclog
    for i in range(KEEP_EVENTS + 120):
        log.event("manifest", "1.2.3.4")
    log.prune()
    n = client.app.state.store.db.one("SELECT COUNT(*) AS n FROM sync_events")["n"]
    assert n == KEEP_EVENTS
