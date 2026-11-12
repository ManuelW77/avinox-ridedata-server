"""Tests für Server-API, Weboberfläche und ``avinox import`` – nur synthetische Daten."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from avinox import cli
from avinox.server import store as store_mod
from avinox.server.app import create_app
from avinox.server.config import load_settings
from ridebuilder import ride
from test_buried import LINES
from test_track import T0, gps_sample

RIDE_PATH = "ride_rec/4242/ride_rec_1000.proto"
BURIED_END = int(datetime(2026, 11, 5, 14, 58, 43, tzinfo=UTC).timestamp())


def ride_bytes(n=30, assist=None) -> bytes:
    samples = [{**gps_sample(t, 5 * t), **({8: assist} if assist is not None else {})} for t in range(n)]
    return ride(samples, start=T0, end=T0 + n, distance=5 * n)


def buried_ride_bytes() -> bytes:
    return ride([], start=BURIED_END - 7712, end=BURIED_END, count=7643, distance=18368)


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


def auth(settings):
    return {"Authorization": f"Bearer {settings.upload_token}"}


def put(client, settings, data, path=RIDE_PATH, offset=0, size=None):
    return client.put(
        "/api/v1/upload",
        params={"path": path, "size": len(data) if size is None else size, "offset": offset},
        content=data,
        headers=auth(settings),
    )


def test_upload_auth(client, settings):
    data = ride_bytes()
    params = {"path": RIDE_PATH, "size": len(data)}
    assert client.put("/api/v1/upload", params=params, content=data).status_code == 401
    assert client.put("/api/v1/upload", params=params, content=data, headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert client.get("/api/v1/manifest").status_code == 401
    assert put(client, settings, data).status_code == 200


def test_resumable_upload(client, settings):
    data = ride_bytes()
    size = len(data)
    a, b = size // 3, 2 * size // 3
    r = put(client, settings, data[:a], size=size)
    assert r.status_code == 200 and r.json()["received"] == a and not r.json()["complete"]

    r = put(client, settings, data[b:], offset=b, size=size)
    assert r.status_code == 409 and r.json()["received"] == a

    status = client.get("/api/v1/upload/status", params={"path": RIDE_PATH, "size": size}, headers=auth(settings)).json()
    assert status["received"] == a and not status["complete"]

    assert put(client, settings, data[a:b], offset=a, size=size).json()["received"] == b
    r = put(client, settings, data[b:], offset=b, size=size).json()
    assert r["complete"] is True and r["ride"]

    status = client.get("/api/v1/upload/status", params={"path": RIDE_PATH, "size": size}, headers=auth(settings)).json()
    assert status["complete"] is True
    files = client.get("/api/v1/manifest", headers=auth(settings)).json()["files"]
    assert {"path": RIDE_PATH, "size": size} in files

    again = put(client, settings, data).json()
    assert again["complete"] is True
    res = client.post("/api/v1/files", files=[("files", (RIDE_PATH, data))]).json()["results"][0]
    assert res["ok"] and res["new"] is False
    assert len(client.get("/api/v1/rides").json()) == 1


@pytest.mark.parametrize("path", ["ebike_log_1_x.dat", "../etc/passwd", "ride_rec/../../x.proto"])
def test_forbidden_paths(client, settings, path):
    assert put(client, settings, b"abc", path=path).status_code == 400


def test_browser_upload_normalizes_prefix(client):
    files = [
        ("files", ("Avinox/ride_rec/4242/ride_rec_1000.proto", ride_bytes())),
        ("files", ("ride_rec/4242/ride_rec_1001.proto", ride_bytes(20))),
        ("files", ("ebike_log_1_19700101_0000.dat", b"x")),
    ]
    results = client.post("/api/v1/files", files=files).json()["results"]
    assert [r["ok"] for r in results] == [True, True, False]
    paths = {r["path"] for r in client.get("/api/v1/rides").json()}
    assert paths == {RIDE_PATH, "ride_rec/4242/ride_rec_1001.proto"}


def test_read_api(client, settings):
    pk = put(client, settings, ride_bytes()).json()["ride"]
    rides = client.get("/api/v1/rides").json()
    assert [r["id"] for r in rides] == [pk]
    detail = client.get(f"/api/v1/rides/{pk}").json()
    assert "analysis" in detail and detail["path"] == RIDE_PATH

    geo = client.get(f"/api/v1/rides/{pk}/geojson")
    assert geo.status_code == 200 and geo.json()["type"]
    gpx = client.get(f"/api/v1/rides/{pk}/gpx")
    assert gpx.status_code == 200 and "attachment" in gpx.headers["content-disposition"] and ".gpx" in gpx.headers["content-disposition"]
    assert gpx.text.lstrip().startswith("<?xml")
    raw = client.get(f"/api/v1/rides/{pk}/raw")
    assert raw.content == ride_bytes()

    assert client.get("/api/v1/stats").json()["total"]["rides"] == 1
    assert client.get("/api/v1/device").status_code == 200
    for suffix in ("", "/geojson", "/gpx", "/raw"):
        assert client.get(f"/api/v1/rides/9999{suffix}").status_code == 404


def test_mode_names(client, settings):
    pk = put(client, settings, ride_bytes(assist=21)).json()["ride"]
    before = client.get(f"/api/v1/rides/{pk}").json()["analysis"]["assist"]
    assert "eigen_21" in before["seconds_per_mode"]
    assert client.put("/api/v1/settings/modes", json={"21": "Tour"}).status_code == 200
    after = client.get(f"/api/v1/rides/{pk}").json()["analysis"]["assist"]
    assert "Tour" in after["seconds_per_mode"] and "eigen_21" not in after["seconds_per_mode"]
    assert client.get("/api/v1/settings/modes").json()["names"] == {"21": "Tour"}
    assert client.put("/api/v1/settings/modes", json={"abc": "X"}).status_code == 400


def test_basic_auth(settings):
    settings.web_user, settings.web_password = "fahrer", "geheim"
    with TestClient(create_app(settings)) as c:
        assert c.get("/").status_code == 401
        assert c.get("/api/v1/rides").status_code == 401
        assert c.get("/", auth=("fahrer", "falsch")).status_code == 401
        assert c.get("/", auth=("fahrer", "geheim")).status_code == 200
        data = ride_bytes()
        assert put(c, settings, data).status_code == 200
        assert c.put("/api/v1/upload", params={"path": RIDE_PATH, "size": len(data)}, content=data, auth=("fahrer", "geheim")).status_code == 401


def test_basic_auth_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AVINOX_WEB_USER", "a")
    monkeypatch.setenv("AVINOX_WEB_PASSWORD", "b")
    with TestClient(create_app(load_settings(tmp_path))) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/").status_code == 401
        assert c.get("/", auth=("a", "b")).status_code == 200


def test_pages(client, settings):
    pk = put(client, settings, ride_bytes(assist=21)).json()["ride"]
    client.post("/api/v1/files", files=[("files", ("buried_rec/buried_file_cur.json", LINES.encode()))])
    for url in ("/", "/rides", f"/rides/{pk}", "/device", "/settings"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert "text/html" in r.headers["content-type"]
    assert client.get("/rides/9999").status_code == 404


def test_settings_lists_upload_files(client):
    from avinox.server.store import PATTERNS, UPLOAD_FILES

    page = client.get("/settings").text
    assert "ride_rec_index.dat" in page
    assert "ebike_log_" in page
    assert {f["kind"] for f in UPLOAD_FILES} == set(PATTERNS)


def test_pages_empty(client):
    for url in ("/", "/rides", "/device", "/settings"):
        assert client.get(url).status_code == 200, url


def test_device_stats_assigned_after_buried_upload(client, settings):
    pk = put(client, settings, buried_ride_bytes()).json()["ride"]
    assert client.get(f"/api/v1/rides/{pk}").json()["has_device_stats"] == 0
    r = put(client, settings, LINES.encode(), path="buried_rec/buried_file_cur.json")
    assert r.json()["complete"] is True
    detail = client.get(f"/api/v1/rides/{pk}").json()
    assert detail["has_device_stats"] == 1
    assert detail["analysis"]["device_stats"] is not None
    assert client.get("/api/v1/device").json()["charge_cycles"] == 3


def test_reprocess_on_parser_version(settings, monkeypatch):
    with TestClient(create_app(settings)) as c:
        pk = put(c, settings, ride_bytes()).json()["ride"]
        assert c.get(f"/api/v1/rides/{pk}").json()["parser_version"] == store_mod.PARSER_VERSION
    old = store_mod.PARSER_VERSION
    monkeypatch.setattr(store_mod, "PARSER_VERSION", old + 1)
    with TestClient(create_app(settings)) as c:
        assert c.get(f"/api/v1/rides/{pk}").json()["parser_version"] == old + 1


def test_localtime_weekday_german(client):
    f = client.app.state.templates.env.filters["localtime"]
    # 2026-11-09 ist ein Montag; 23:30 UTC = Dienstag 00:30 in Berlin
    assert f("2026-11-09T10:00:00+00:00", "%a %d.%m.") == "Mo 09.11."
    assert f("2026-11-09T23:30:00+00:00", "%a %H:%M") == "Di 00:30"
    assert f(None) == "ohne Uhrzeit"


def test_cli_import(tmp_path, capsys):
    drive = tmp_path / "laufwerk"
    (drive / "ride_rec" / "4242").mkdir(parents=True)
    (drive / "buried_rec").mkdir()
    (drive / "ride_rec" / "4242" / "ride_rec_1000.proto").write_bytes(ride_bytes())
    (drive / "ride_rec" / "4242" / "ride_rec_1001.proto").write_bytes(buried_ride_bytes())
    (drive / "buried_rec" / "buried_file_cur.json").write_text(LINES)
    (drive / "ebike_log_1_19700101_0000.dat").write_bytes(b"verschluesselt")
    data_dir = tmp_path / "data"

    assert cli.main(["import", str(drive), "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "neu: 3, bekannt: 0, übersprungen: 1" in out and "Fahrten: 2" in out

    assert cli.main(["import", str(drive), "--data-dir", str(data_dir)]) == 0
    assert "neu: 0, bekannt: 3, übersprungen: 1" in capsys.readouterr().out
    assert "upload_token" not in out


def test_upload_limits_and_token_visibility(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from avinox.server.app import create_app
    from avinox.server.config import load_settings

    monkeypatch.setenv("AVINOX_MAX_UPLOAD_MB", "1")
    settings = load_settings(tmp_path)
    client = TestClient(create_app(settings))
    auth = {"Authorization": f"Bearer {settings.upload_token}"}
    path = "ride_rec/4242/ride_rec_1000.proto"
    # Stück größer als angekündigte Restgröße -> 413, bevor gelesen wird
    r = client.put(f"/api/v1/upload?path={path}&size=10&offset=0", content=b"x" * 11, headers=auth)
    assert r.status_code == 413
    # Gesamtgröße über dem Limit -> 413
    big = 2 * 1024 * 1024
    r = client.put(f"/api/v1/upload?path={path}&size={big}&offset=0", content=b"x" * (1024 * 1024 + 1), headers=auth)
    assert r.status_code == 413
    # Browser-Upload über dem Limit -> 413
    r = client.post("/api/v1/files", files={"files": (path, b"x" * (3 * 1024 * 1024))})
    assert r.status_code == 413
    # ohne Web-Passwort kein Token auf der Seite
    page = client.get("/settings").text
    assert settings.upload_token not in page and "upload_token" in page


def test_token_shown_with_web_login(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from avinox.server.app import create_app
    from avinox.server.config import load_settings

    monkeypatch.setenv("AVINOX_WEB_USER", "u")
    monkeypatch.setenv("AVINOX_WEB_PASSWORD", "p")
    settings = load_settings(tmp_path)
    client = TestClient(create_app(settings))
    assert settings.upload_token in client.get("/settings", auth=("u", "p")).text
