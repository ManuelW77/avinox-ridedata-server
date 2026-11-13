"""Tests für OTA-Firmware – synthetische Images."""

import base64
import hashlib
import struct

import pytest
from conftest import web_login
from fastapi.testclient import TestClient

from avinox.cli import main
from avinox.server.app import create_app
from avinox.server.config import load_settings
from avinox.server.firmware import read_app_info
from test_server import auth


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("AVINOX_UPLOAD_TOKEN", "AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD", "AVINOX_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)


def image(version="0.3.0", project="avinox-sync", extra=b""):
    desc = struct.pack("<II8s32s32s16s16s32s32s", 0xABCD5432, 0, b"", version.encode(), project.encode(), b"12:00:00", b"Nov 12 2026", b"v5.4", b"\1" * 32)
    return bytes([0xE9]) + b"\0" * 31 + desc + b"rest" * 100 + extra


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path / "data")


@pytest.fixture
def client(settings):
    # Firmware-Verwaltung verlangt ein Web-Passwort; der Test-Client meldet sich an.
    settings.web_user, settings.web_password = "fahrer", "geheim"
    with TestClient(create_app(settings)) as c:
        c.headers["Authorization"] = "Basic " + base64.b64encode(b"fahrer:geheim").decode()
        yield c


def upload(client, data, name="avinox-sync.bin"):
    return client.post("/api/v1/firmware", files={"file": (name, data)})


def test_read_app_info():
    info = read_app_info(image())
    assert info["version"] == "0.3.0" and info["project_name"] == "avinox-sync" and info["idf_ver"] == "v5.4"
    for bad in (b"", b"\x00" * 400, image()[:100], b"\xe9" + b"\0" * 400, image(extra=b"x" * 2_100_000)):
        with pytest.raises(ValueError):
            read_app_info(bad)


def test_upload_and_latest(client, settings):
    data = image()
    r = upload(client, data)
    assert r.status_code == 200 and r.json()["version"] == "0.3.0" and r.json()["project"] == "avinox-sync"
    assert upload(client, b"kein image").status_code == 400
    assert client.get("/api/v1/firmware/latest").status_code == 401
    r = client.get("/api/v1/firmware/latest", params={"project": "avinox-sync"}, headers=auth(settings))
    sha = hashlib.sha256(data).hexdigest()
    assert r.json() == {"version": "0.3.0", "size": len(data), "sha256": sha, "url": f"/api/v1/firmware/{sha}.bin"}
    assert client.get("/api/v1/firmware/latest", params={"project": "other"}, headers=auth(settings)).status_code == 404
    d = client.get(r.json()["url"], headers=auth(settings))
    assert d.content == data and d.headers["content-type"] == "application/octet-stream" and d.headers["content-length"] == str(len(data))
    assert client.get(r.json()["url"]).status_code == 401
    assert client.get("/api/v1/firmware/" + "0" * 64 + ".bin", headers=auth(settings)).status_code == 404


def test_latest_404_when_empty(client, settings):
    assert client.get("/api/v1/firmware/latest", headers=auth(settings)).status_code == 404


def test_versions_current_delete(client, settings):
    first = upload(client, image("0.2.0")).json()
    second = upload(client, image("0.3.0")).json()
    cur = lambda: client.get("/api/v1/firmware/latest", headers=auth(settings)).json()["version"]
    assert cur() == "0.3.0"
    assert client.delete(f"/api/v1/firmware/{second['id']}").status_code == 409
    assert client.post(f"/api/v1/firmware/{first['id']}/current").status_code == 200 and cur() == "0.2.0"
    assert client.delete(f"/api/v1/firmware/{second['id']}").status_code == 200
    assert client.get(f"/api/v1/firmware/{second['sha256']}.bin", headers=auth(settings)).status_code == 404
    assert client.delete("/api/v1/firmware/999").status_code == 404
    # gleiche Version erneut = ersetzt
    again = upload(client, image("0.2.0", extra=b"neu")).json()
    assert again["sha256"] != first["sha256"] and len(client.app.state.store.list_firmware()) == 1


def test_events_and_ota_report(client, settings):
    upload(client, image())
    h = {**auth(settings), "X-Avinox-Device": "esp-1"}
    latest = client.get("/api/v1/firmware/latest", headers=h).json()
    client.get(latest["url"], headers=h)
    ev = client.get("/api/v1/sync/log").json()["events"]
    assert [e["kind"] for e in ev[:2]] == ["firmware_download", "firmware_check"]
    assert ev[0]["device_id"] == "esp-1" and ev[0]["detail"] == "0.3.0"
    body = {"device_id": "esp-1", "event": "ota", "firmware": "0.2.0", "ota": {"from": "0.2.0", "to": "0.3.0", "result": "failed", "error": "Prüfsumme"}}
    assert client.post("/api/v1/device/report", json=body, headers=auth(settings)).status_code == 200
    e = client.get("/api/v1/sync/log").json()["events"][0]
    assert e["kind"] == "ota" and "0.2.0" in e["detail"] and "fehlgeschlagen" in e["detail"] and "Prüfsumme" in e["detail"]
    for bad in ({"device_id": "esp-1", "event": "ota"}, {**body, "ota": {"result": "kaputt"}}):
        assert client.post("/api/v1/device/report", json=bad, headers=auth(settings)).status_code == 400
    web_login(client, "fahrer", "geheim")  # Seiten brauchen eine Session
    page = client.get("/sync").text
    assert "Firmware-Update" in page and "verfügbar 0.3.0" in page


def test_settings_page(client):
    web_login(client, "fahrer", "geheim")  # Seiten brauchen eine Session
    assert "Noch keine Firmware" in client.get("/settings").text
    # Mit Session greift der CSRF-Schutz: fetch im Browser schickt Origin mit
    client.post("/api/v1/firmware", files={"file": ("avinox-sync.bin", image())}, headers={"Origin": "http://testserver"})
    html = client.get("/settings").text
    assert "Firmware für den ESP32" in html and "avinox-sync 0.3.0" in html


def test_cli(tmp_path, capsys):
    f = tmp_path / "a.bin"
    f.write_bytes(image())
    d = str(tmp_path / "d")
    assert main(["firmware", "add", str(f), "--data-dir", d]) == 0
    assert main(["firmware", "list", "--data-dir", d]) == 0
    assert "avinox-sync 0.3.0" in capsys.readouterr().out
    bad = tmp_path / "b.bin"
    bad.write_bytes(b"x")
    assert main(["firmware", "add", str(bad), "--data-dir", d]) == 1


def test_admin_needs_web_password(settings):
    with TestClient(create_app(settings)) as c:  # kein Web-Passwort konfiguriert
        assert upload(c, image()).status_code == 403
        assert c.post("/api/v1/firmware/1/current").status_code == 403
        assert c.delete("/api/v1/firmware/1").status_code == 403
        assert "Anmeldung" in c.delete("/api/v1/firmware/1").json()["detail"]


def test_admin_needs_login(settings):
    settings.web_user, settings.web_password = "fahrer", "geheim"
    with TestClient(create_app(settings)) as c:  # Passwort gesetzt, aber nicht angemeldet
        assert upload(c, image()).status_code == 401
        assert c.post("/api/v1/firmware/1/current").status_code == 401
        assert c.delete("/api/v1/firmware/1").status_code == 401
        assert c.post("/api/v1/firmware/1/current", auth=("fahrer", "falsch")).status_code == 401
        assert c.post("/api/v1/firmware/1/current", auth=("fahrer", "geheim")).status_code == 404


def test_settings_page_hides_admin_without_password(settings):
    with TestClient(create_app(settings)) as c:
        html = c.get("/settings").text
        assert 'id="fw-form"' not in html and "Firmware-Verwaltung nur mit Web-Passwort" in html
