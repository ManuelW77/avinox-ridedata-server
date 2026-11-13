"""MCP-Server: Token-Zugang, Werkzeuge, keine sensiblen Schlüssel. Nur synthetische Daten."""

import json

import pytest
from fastapi.testclient import TestClient

from avinox.server import ai
from avinox.server.app import create_app
from avinox.server.config import load_settings
from test_ai import add_ride, keys_deep

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
FORBIDDEN = {"lat", "lon", "lng", "latitude", "longitude", "serial", "serial_number", "geojson", "track", "coordinates", "path", "file",
             "filename", "username", "email", "token", "api_key"}
INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai, "_post", lambda *a, **k: pytest.fail("echtes Netz"))
    app = create_app(load_settings(tmp_path / "data"))
    app.state.ai_paused = True
    with TestClient(app) as c:
        yield c


@pytest.fixture
def store(client):
    return client.app.state.store


def enable(client) -> str:
    r = client.put("/api/v1/settings/mcp", json={"enabled": True})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def rpc(client, method, params=None, *, token=None, path="/mcp", headers=None):
    h = {**HDR, **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(path, content=json.dumps(body), headers=h)


def call(client, name, args, token):
    r = rpc(client, "tools/call", {"name": name, "arguments": args}, token=token)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_disabled_is_404(client):
    assert rpc(client, "initialize", INIT).status_code == 404
    assert rpc(client, "initialize", INIT, path="/mcp/irgendwas").status_code == 404


def test_token_required(client):
    token = enable(client)
    r = rpc(client, "initialize", INIT)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert rpc(client, "initialize", INIT, token="falsch").status_code == 401
    assert rpc(client, "initialize", INIT, path="/mcp/falsch").status_code == 401
    assert rpc(client, "initialize", INIT, token=token[:-1]).status_code == 401


def test_bearer_and_path_token_work(client):
    token = enable(client)
    r = rpc(client, "initialize", INIT, token=token)
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "avinox"
    assert rpc(client, "initialize", INIT, path=f"/mcp/{token}").status_code == 200


def test_tools_list_and_calls(client, store):
    token = enable(client)
    names = {t["name"] for t in rpc(client, "tools/list", token=token).json()["result"]["tools"]}
    assert names == {"list_rides", "get_ride", "get_period", "get_bests", "get_bike_status"}
    pk = add_ride(store)
    rides = call(client, "list_rides", {"limit": 5}, token)
    assert rides["total"] == 1 and rides["rides"][0]["id"] == pk and rides["rides"][0]["distance_km"] > 0
    assert call(client, "list_rides", {"date_from": "2999-01-01"}, token)["rides"] == []
    detail = call(client, "get_ride", {"ride_id": pk}, token)
    assert detail["id"] == pk and isinstance(detail["figures"], dict)
    outs = [rides, detail, call(client, "get_period", {"period": "all"}, token), call(client, "get_bests", {}, token),
            call(client, "get_bike_status", {}, token)]
    assert "bests" in outs[3]
    for out in outs:
        assert not FORBIDDEN & set(keys_deep(out))


def test_argument_validation(client):
    token = enable(client)
    for name, args in [("list_rides", {"limit": 0}), ("list_rides", {"limit": 201}), ("list_rides", {"offset": -1}),
                       ("list_rides", {"date_from": "morgen"}), ("get_period", {"period": "decade"}), ("get_ride", {"ride_id": 999})]:
        r = rpc(client, "tools/call", {"name": name, "arguments": args}, token=token)
        body = r.json()
        assert body.get("error") or body["result"].get("isError"), (name, args)


def test_token_rotation_invalidates_old(client):
    old = enable(client)
    assert rpc(client, "tools/list", token=old).status_code == 200
    new = client.post("/api/v1/settings/mcp/token").json()["token"]
    assert new != old
    assert rpc(client, "tools/list", token=old).status_code == 401
    assert rpc(client, "tools/list", path=f"/mcp/{old}").status_code == 401
    assert rpc(client, "tools/list", token=new).status_code == 200


def test_settings_view(client):
    d = client.get("/api/v1/settings/mcp").json()
    assert d["enabled"] is False and d["token"] is None
    token = enable(client)
    d = client.get("/api/v1/settings/mcp").json()
    assert d["enabled"] and d["url"].endswith("/mcp") and d["url_with_token"] == f"{d['url']}/{token}"
    assert client.put("/api/v1/settings/mcp", json={"enabled": False}).json()["enabled"] is False
    assert client.put("/api/v1/settings/mcp", json={"enabled": "ja"}).status_code == 400
    assert rpc(client, "tools/list", token=token).status_code == 404


def login_client(tmp_path, monkeypatch):
    for k in ("AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    settings = load_settings(tmp_path / "data")
    settings.web_user, settings.web_password = "admin", "admin-passwort-1"
    app = create_app(settings)
    app.state.ai_paused = True
    return app


def test_not_blocked_by_login(tmp_path, monkeypatch):
    app = login_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        token = c.put("/api/v1/settings/mcp", json={"enabled": True}, auth=("admin", "admin-passwort-1")).json()["token"]
        r = rpc(c, "tools/list", token=token, headers={"Origin": "https://fremd.example"})
        assert r.status_code == 200  # weder Login-Weiterleitung noch CSRF-/Origin-Prüfung
        assert rpc(c, "tools/list").status_code == 401


def test_settings_admin_only_with_login(tmp_path, monkeypatch):
    app = login_client(tmp_path, monkeypatch)
    with TestClient(app) as c:
        app.state.auth.create_user("anna", "anna-passwort")  # kein Admin
        anna, admin = ("anna", "anna-passwort"), ("admin", "admin-passwort-1")
        assert c.get("/api/v1/settings/mcp", auth=anna).status_code == 403  # das Token steht darin
        assert c.put("/api/v1/settings/mcp", json={"enabled": True}, auth=anna).status_code == 403
        assert c.post("/api/v1/settings/mcp/token", auth=anna).status_code == 403
        assert c.get("/api/v1/settings/mcp").status_code in (401, 403)
        assert c.put("/api/v1/settings/mcp", json={"enabled": True}, auth=admin).status_code == 200
        assert c.get("/api/v1/settings/mcp", auth=admin).json()["token"]
