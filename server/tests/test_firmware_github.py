"""Tests für den Firmware-Import aus GitHub-Releases – ohne echtes Netz."""

import base64

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_firmware import clean_env, image  # noqa: F401

from avinox.server import firmware_github as fg
from avinox.server.app import create_app
from avinox.server.config import load_settings
from avinox.server.store import Store

ASSET_URL = "https://github.com/ManuelW77/avinox-ridedata-server/releases/download/fw-v0.6.3/avinox-sync.bin"


def release(tag, draft=False, prerelease=False, name="avinox-sync.bin", size=500, url=ASSET_URL):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": [{"name": name, "size": size, "browser_download_url": url}]}


def fake_github(monkeypatch, releases, files=None):
    """files: URL -> (Status, Header, Body) oder Bytes."""
    files = files if files is not None else {ASSET_URL: image("0.6.3")}

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.startswith("https://api.github.com/"):
            return httpx2.Response(200, json=releases)
        item = files.get(url)
        if item is None:
            return httpx2.Response(404)
        if isinstance(item, bytes):
            return httpx2.Response(200, content=item)
        status, headers, body = item
        return httpx2.Response(status, headers=headers, content=body)

    monkeypatch.setattr(fg, "TRANSPORT", httpx2.MockTransport(handler))


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path / "data")


@pytest.fixture
def store(settings):
    s = Store(settings)
    yield s
    s.close()


def test_picks_fw_not_server_and_highest(monkeypatch):
    fake_github(monkeypatch, [release("server-v9.0.0"), release("fw-v0.6.3"), release("fw-v0.10.0"), release("fw-v0.9.9")])
    assert fg.fetch_latest_fw_release()["tag_name"] == "fw-v0.10.0"


def test_ignores_draft_and_prerelease(monkeypatch):
    fake_github(monkeypatch, [release("fw-v1.0.0", draft=True), release("fw-v0.9.0", prerelease=True), release("fw-v0.6.3")])
    assert fg.fetch_latest_fw_release()["tag_name"] == "fw-v0.6.3"


def test_no_release(monkeypatch, store):
    fake_github(monkeypatch, [release("server-v1.0.0")])
    assert fg.import_latest_release(store)["status"] == "none"


def test_import_ok(monkeypatch, store):
    fake_github(monkeypatch, [release("fw-v0.6.3")])
    res = fg.import_latest_release(store)
    assert res["status"] == "imported" and res["version"] == "0.6.3" and res["release"] == "fw-v0.6.3"
    assert store.current_firmware("avinox-sync")["version"] == "0.6.3"


def test_missing_asset(monkeypatch, store):
    fake_github(monkeypatch, [release("fw-v0.6.3", name="avinox-sync-full.bin")])
    res = fg.import_latest_release(store)
    assert res["status"] == "failed" and "avinox-sync.bin" in res["message"]


def test_bad_host_redirect_rejected(monkeypatch, store):
    files = {ASSET_URL: (302, {"location": "https://evil.example.com/x.bin"}, b"")}
    fake_github(monkeypatch, [release("fw-v0.6.3")], files)
    res = fg.import_latest_release(store)
    assert res["status"] == "failed" and "Host" in res["message"]
    assert store.list_firmware() == []


def test_http_redirect_rejected(monkeypatch, store):
    files = {ASSET_URL: (302, {"location": "http://objects.githubusercontent.com/x.bin"}, b"")}
    fake_github(monkeypatch, [release("fw-v0.6.3")], files)
    assert fg.import_latest_release(store)["status"] == "failed"


def test_good_redirect_followed(monkeypatch, store):
    cdn = "https://objects.githubusercontent.com/x.bin"
    fake_github(monkeypatch, [release("fw-v0.6.3")], {ASSET_URL: (302, {"location": cdn}, b""), cdn: image("0.6.3")})
    assert fg.import_latest_release(store)["status"] == "imported"


def test_too_big_by_size_and_by_stream(monkeypatch, store):
    fake_github(monkeypatch, [release("fw-v0.6.3", size=3_000_000)])
    assert fg.import_latest_release(store)["status"] == "failed"
    fake_github(monkeypatch, [release("fw-v0.6.3")], {ASSET_URL: image("0.6.3", extra=b"x" * 2_100_000)})
    res = fg.import_latest_release(store)
    assert res["status"] == "failed" and "2 MB" in res["message"]


def test_invalid_image(monkeypatch, store):
    fake_github(monkeypatch, [release("fw-v0.6.3")], {ASSET_URL: b"kein image"})
    res = fg.import_latest_release(store)
    assert res["status"] == "failed" and store.list_firmware() == []


def test_github_unreachable(monkeypatch, store):
    def boom(request):
        raise httpx2.ConnectError("nope")

    monkeypatch.setattr(fg, "TRANSPORT", httpx2.MockTransport(boom))
    res = fg.import_latest_release(store)
    assert res["status"] == "failed" and "erreichbar" in res["message"]


def test_exists_keeps_rollback(monkeypatch, store):
    fake_github(monkeypatch, [release("fw-v0.6.3")])
    assert fg.import_latest_release(store)["status"] == "imported"
    store.add_firmware(image("0.6.2"))  # manueller Rollback: 0.6.2 ist jetzt aktuell
    assert store.current_firmware("avinox-sync")["version"] == "0.6.2"
    res = fg.import_latest_release(store)
    assert res["status"] == "exists"
    assert store.current_firmware("avinox-sync")["version"] == "0.6.2"


def test_endpoint_requires_admin(monkeypatch, settings):
    fake_github(monkeypatch, [release("fw-v0.6.3")])
    settings.web_user, settings.web_password = "fahrer", "geheim"
    with TestClient(create_app(settings)) as c:
        assert c.post("/api/v1/firmware/import-github").status_code == 401
        assert c.put("/api/v1/settings/firmware-auto-import", json={"enabled": True}).status_code == 401
        c.headers["Authorization"] = "Basic " + base64.b64encode(b"fahrer:geheim").decode()
        r = c.post("/api/v1/firmware/import-github")
        assert r.status_code == 200 and r.json()["status"] == "imported"
        assert c.get("/api/v1/settings/firmware-auto-import").json() == {"enabled": False}
        assert c.put("/api/v1/settings/firmware-auto-import", json={"enabled": True}).json() == {"enabled": True}
        assert c.put("/api/v1/settings/firmware-auto-import", json={"enabled": "x"}).status_code == 400
