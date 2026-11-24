"""Tests für den Update-Hinweis (Server-Releases auf GitHub) – ohne echtes Netz."""

import base64

import httpx2
from fastapi.testclient import TestClient
from conftest import web_login
from test_firmware import clean_env, settings  # noqa: F401

from avinox.server import app as app_mod
from avinox.server import firmware_github as fg
from avinox.server import update_check as uc
from avinox.server.app import create_app
from avinox.server.store import Store

URL = "https://github.com/ManuelW77/avinox-ridedata-server/releases/tag/server-v1.5.0"


def rel(tag, draft=False, prerelease=False, url=None):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "html_url": url or f"https://github.com/x/y/releases/tag/{tag}"}


def fake(monkeypatch, releases):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx2.Response(200, json=releases)

    monkeypatch.setattr(fg, "TRANSPORT", httpx2.MockTransport(handler))
    return calls


def test_filter_server_vs_fw():
    rs = [rel("fw-v9.0.0"), rel("server-v1.4.1"), rel("server-v1.10.0"), rel("server-v1.9.0"), rel("server-vx")]
    assert fg.latest_release(rs, fg.SERVER_TAG_RE)["tag_name"] == "server-v1.10.0"
    assert fg.latest_release(rs, fg.TAG_RE)["tag_name"] == "fw-v9.0.0"


def test_draft_and_prerelease_ignored():
    rs = [rel("server-v2.0.0", draft=True), rel("server-v1.9.0", prerelease=True), rel("server-v1.5.0")]
    assert fg.latest_release(rs, fg.SERVER_TAG_RE)["tag_name"] == "server-v1.5.0"
    assert fg.latest_release([rel("server-v2.0.0", draft=True)], fg.SERVER_TAG_RE) is None


def test_is_newer_numeric():
    assert uc.is_newer("1.10.0", "1.9.0")
    assert not uc.is_newer("1.4.1", "1.4.1")
    assert not uc.is_newer("1.4.0", "1.4.1")
    assert not uc.is_newer("1.5.0", "dev")
    assert not uc.is_newer("abc", "1.0.0")


def page_app(monkeypatch, settings, current, releases):
    monkeypatch.setattr(app_mod, "APP_VERSION", current)
    fake(monkeypatch, releases)
    return TestClient(create_app(settings))


def seed(settings, releases):
    store = Store(settings)
    uc.run(store, releases)
    store.close()


def test_hint_in_page_when_newer(monkeypatch, settings):
    seed(settings, [rel("fw-v9.0.0"), rel("server-v1.5.0", url=URL)])
    with page_app(monkeypatch, settings, "1.4.1", []) as c:
        html = c.get("/settings").text
        assert 'id="update"' in html and "1.5.0" in html
        assert URL in html and "handbuch.html#updates" in html and "Neu:" in html
        assert 'id="update"' in c.get("/").text
    with page_app(monkeypatch, settings, "1.5.0", []) as c:
        html = c.get("/settings").text
        assert 'type="application/json" id="update"' not in html and "Aktuell" in html
    with page_app(monkeypatch, settings, "1.10.0", []) as c:
        assert 'type="application/json" id="update"' not in c.get("/").text
    with page_app(monkeypatch, settings, "dev", []) as c:
        assert 'type="application/json" id="update"' not in c.get("/").text


def test_disable_works(monkeypatch, settings):
    seed(settings, [rel("server-v1.5.0", url=URL)])
    settings.web_user, settings.web_password = "fahrer", "geheim"
    with page_app(monkeypatch, settings, "1.4.1", []) as c:
        web_login(c, "fahrer", "geheim")
        c.headers["Authorization"] = "Basic " + base64.b64encode(b"fahrer:geheim").decode()
        assert 'type="application/json" id="update"' in c.get("/").text
        c.cookies.clear()  # API per Basic, ohne Session (sonst CSRF-Pflicht)
        assert c.get("/api/v1/settings/update-check").json() == {"enabled": True}
        assert c.put("/api/v1/settings/update-check", json={"enabled": False}).json() == {"enabled": False}
        assert c.put("/api/v1/settings/update-check", json={"enabled": "x"}).status_code == 400
        web_login(c, "fahrer", "geheim")
        html = c.get("/").text
        assert "<nav" in html and 'type="application/json" id="update"' not in html


def test_state_stores_result(monkeypatch, settings):
    store = Store(settings)
    assert uc.state(store, "1.4.1")["newer"] is False
    res = uc.run(store, [rel("server-v1.5.0", url=URL), rel("server-v1.4.0")])
    assert res["latest_version"] == "1.5.0" and res["url"] == URL and res["checked_at"]
    st = uc.state(store, "1.4.1")
    assert st["newer"] and st["latest"] == "1.5.0"
    store.db.set_setting(uc.KEY_ENABLED, False)
    assert not uc.state(store, "1.4.1")["newer"]
    store.close()
