"""Tests für Server-API, Weboberfläche und ``avinox import`` – nur synthetische Daten."""

import re
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
    for key in ("AVINOX_UPLOAD_TOKEN", "AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD", "AVINOX_DATA_DIR", "AVINOX_PUBLIC_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AVINOX_GEOCODE_URL", "off")  # nie ins Netz


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


def test_rides_page_toolbar(client, settings):
    put(client, settings, ride_bytes(assist=21))
    page = client.get("/rides").text
    assert 'id="rides-tools"' in page and "data-search=" in page and "rides.js" in page


def test_pages(client, settings):
    pk = put(client, settings, ride_bytes(assist=21)).json()["ride"]
    client.post("/api/v1/files", files=[("files", ("buried_rec/buried_file_cur.json", LINES.encode()))])
    for url in ("/", "/rides", f"/rides/{pk}", "/device", "/settings"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert "text/html" in r.headers["content-type"]
    page = client.get(f"/rides/{pk}").text
    assert "Leistung nach Steigung" in page and "Tempo nach Steigung" in page

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
    # Token ist auch ohne Web-Passwort sichtbar, mit Hinweis
    page = client.get("/settings").text
    assert settings.upload_token in page and "ohne Passwort im WLAN offen" in page


def test_token_shown_with_web_login(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from avinox.server.app import create_app
    from avinox.server.config import load_settings

    monkeypatch.setenv("AVINOX_WEB_USER", "u")
    monkeypatch.setenv("AVINOX_WEB_PASSWORD", "p")
    settings = load_settings(tmp_path)
    client = TestClient(create_app(settings))
    assert settings.upload_token in client.get("/settings", auth=("u", "p")).text


def _urls(html):
    import re

    return re.findall(r'class="server-url" value="([^"]*)"', html)


def test_settings_server_url(tmp_path, monkeypatch):
    monkeypatch.setattr("avinox.server.app._in_docker", lambda: False)
    monkeypatch.setattr("avinox.server.app._lan_ip", lambda: None)
    client = TestClient(create_app(load_settings(tmp_path)))
    html = client.get("/settings", headers={"host": "192.0.2.10:8000"}).text
    assert "http://192.0.2.10:8000" in _urls(html) and "erreicht der ESP32 nicht" not in html
    for host in ("localhost:8000", "127.0.0.1:8000", "172.18.0.2:8000", "[::1]:8000", "0.0.0.0:8000"):
        assert "erreicht der ESP32 nicht" in client.get("/settings", headers={"host": host}).text, host
    html = client.get("/settings", headers={"host": "example.org", "x-forwarded-proto": "https"}).text
    assert "https://example.org" in _urls(html) and "empfohlen" in html
    html = client.get("/settings", headers={"host": "example.org", "x-forwarded-proto": "javascript"}).text
    assert "http://example.org" in _urls(html)
    monkeypatch.setenv("AVINOX_PUBLIC_URL", "https://a.example.org/, http://192.0.2.5:8000")
    client = TestClient(create_app(load_settings(tmp_path)))
    urls = _urls(client.get("/settings", headers={"host": "localhost:8000"}).text)
    assert urls[:2] == ["https://a.example.org", "http://192.0.2.5:8000"]


def test_settings_server_url_docker(tmp_path, monkeypatch):
    monkeypatch.setattr("avinox.server.app._in_docker", lambda: True)
    monkeypatch.setattr("avinox.server.app._lan_ip", lambda: "198.51.100.7")
    client = TestClient(create_app(load_settings(tmp_path)))
    html = client.get("/settings", headers={"host": "example.org"}).text
    assert "198.51.100.7" not in html and "Im Docker-Container ist die LAN-IP nicht erkennbar" in html
    html = client.get("/settings", headers={"host": "192.0.2.10:8000"}).text
    assert "http://192.0.2.10:8000" in _urls(html)


# ------------------------------------------------------------ Sprache und Fußzeile

FOOTER_START = "© 2026 Manuel Weiser · Build with "
FOOTER_END = " by Manuel Weiser in Leipzig · "
ICON_LABELS = {"de": ("Herz", "Fahrrad"), "en": ("heart", "bike")}
PAGES = ("/", "/rides", "/device", "/sync", "/settings")


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("lang", ["de", "en"])
def test_footer_on_every_page(client, path, lang):
    html = client.get(path, params={"lang": lang}).text
    assert FOOTER_START in html and FOOTER_END in html
    for label in ICON_LABELS[lang]:
        assert f'role="img" aria-label="{label}"' in html
    assert '<a href="https://manuelw.de" rel="noopener">manuelw.de</a>' in html


def test_default_language_is_german(client):
    r = client.get("/")
    assert '<html lang="de">' in r.text and "Noch keine Fahrten." in r.text
    assert "set-cookie" not in r.headers


def test_lang_query_sets_cookie_and_english(client):
    r = client.get("/", params={"lang": "en"})
    assert '<html lang="en">' in r.text and "No rides yet." in r.text and "Noch keine Fahrten" not in r.text
    cookie = r.headers["set-cookie"]
    assert "lang=en" in cookie and "Max-Age=31536000" in cookie and "SameSite=lax" in cookie
    # Cookie bleibt für Folgeaufrufe ohne ?lang= erhalten
    assert "No rides yet." in client.get("/").text
    # Query schlägt Cookie
    assert "Noch keine Fahrten." in client.get("/", params={"lang": "de"}).text


def test_invalid_lang_ignored(client):
    r = client.get("/", params={"lang": "fr"})
    assert '<html lang="de">' in r.text and "set-cookie" not in r.headers


def test_accept_language(client):
    r = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9,de;q=0.8"})
    assert '<html lang="en">' in r.text
    assert '<html lang="de">' in client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}).text
    assert '<html lang="de">' in client.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).text
    # Cookie schlägt Accept-Language
    client.cookies.set("lang", "de")
    assert '<html lang="de">' in client.get("/", headers={"Accept-Language": "en"}).text


def test_language_switcher_keeps_query(client):
    html = client.get("/", params={"period": "week", "lang": "en"}).text
    assert 'href="?period=week&amp;lang=de"' in html and 'href="?period=week&amp;lang=en"' in html


def test_pages_render_in_english_with_data(client, settings):
    assert put(client, settings, ride_bytes()).status_code == 200
    pages = {p: client.get(p, params={"lang": "en"}) for p in (*PAGES, "/rides/1")}
    for p, r in pages.items():
        assert r.status_code == 200, p
        assert 'id="i18n"' in r.text
    assert "Fahrten" not in pages["/rides"].text
    assert "Elevation gain" in pages["/rides/1"].text
    assert "Abgleich" not in pages["/sync"].text


def test_dashboard_api_texts_follow_language(client, settings):
    put(client, settings, ride_bytes())
    de = client.get("/api/v1/dashboard", params={"period": "week"}).json()
    en = client.get("/api/v1/dashboard", params={"period": "week", "lang": "en"}).json()
    assert de["current"]["name"] == "Diese Woche" and en["current"]["name"] == "This week"
    assert set(de) == set(en)


def test_all_keys_in_both_languages():
    import string

    from avinox.server import i18n

    assert set(i18n.TEXTS["de"]) == set(i18n.TEXTS["en"])
    for key in i18n.TEXTS["de"]:
        names = {lang: {f for _, f, _, _ in string.Formatter().parse(i18n.TEXTS[lang][key]) if f} for lang in i18n.LANGS}
        assert names["de"] == names["en"], key
    assert i18n.js_texts("de").keys() == i18n.js_texts("en").keys()


def test_used_keys_exist():
    """Jeder in Templates, Code und Skripten verwendete Schlüssel steht im Wörterbuch."""
    import re
    from pathlib import Path

    from avinox.server import i18n

    base = Path(i18n.__file__).parent
    known = set(i18n.TEXTS["de"])
    dynamic = ("best.", "bestp.", "cur.", "last.", "versus.", "sync.kind.", "upload.kind.", "notup.", "span.", "bh.hint.", "bh.status.")
    for path in [*base.glob("templates/*.html"), base / "app.py", base / "dashboard.py"]:
        for key in re.findall(r"\bt\(\s*['\"]([a-z0-9_.]+)['\"]", path.read_text()):
            assert key in known or key.startswith(dynamic), (path.name, key)
    for path in base.glob("static/*.js"):
        for key in re.findall(r"\btr\(\s*\"([a-z0-9_.]+)\"", path.read_text()):
            assert f"js.{key}" in known or f"js.{key}".startswith("js.span."), (path.name, key)


def test_static_urls_are_root_relative(client):
    html = client.get("/", headers={"host": "example.org", "x-forwarded-proto": "https"}).text
    assert 'href="/static/app.css"' in html
    assert not re.search(r'(?:href|src)="https?://[^"]*/static/', html)


def test_version_in_footer_and_openapi(client, settings):
    from importlib.metadata import version

    v = version("avinox-ridedata-server")
    html = client.get("/", auth=("a", "b")).text if client.get("/").status_code == 401 else client.get("/").text
    assert f"v{v}" in html
    assert client.get("/api/openapi.json", auth=("a", "b")).json()["info"]["version"] == v


def test_settings_language_select(client):
    r = client.get("/settings?lang=en")
    assert r.status_code == 200
    assert 'id="lang-select"' in r.text
    assert '<option value="en" selected' in r.text
    assert client.cookies.get("lang") == "en" or "lang=en" in r.headers.get("set-cookie", "")


def test_device_page_renders_battery_health(client):
    page = client.get("/device").text
    assert "Hinweise" in page and "Alles unauff" in page
    assert client.get("/api/v1/device").json()["battery_health"]["charge_count"] == 0


def test_device_page_battery_charts(client, monkeypatch):
    real = store_mod.Store.device

    nulls = {k: None for k in ("start_temp_c", "end_temp_c", "min_cell_mv", "max_cell_mv", "min_total_v")}

    def fake(self):
        info = real(self)
        info["battery_history"] = [
            {"time": None, "boot_count": 1, "start_pct": 70, "end_pct": 43, "max_temp_c": 17, "max_cell_delta_mv": 200, "max_current_a": 40.0, **nulls},
            {"time": "2026-01-05T10:00:00+00:00", "boot_count": 2, "start_pct": 90, "end_pct": 60, "max_temp_c": 20, "max_cell_delta_mv": 150, "max_current_a": 35.5, **nulls},
        ]
        return info

    monkeypatch.setattr(store_mod.Store, "device", fake)
    page = client.get("/device").text
    assert 'id="bat-data"' in page and "battery.js" in page and 'id="bat-charts"' in page


def test_delete_ride_stays_deleted(settings):
    data = ride_bytes()
    with TestClient(create_app(settings)) as c:
        pk = put(c, settings, data).json()["ride"]
        assert c.delete(f"/api/v1/rides/{pk}").json() == {"ok": True}
        assert c.get(f"/api/v1/rides/{pk}").status_code == 404
        assert c.get("/api/v1/rides").json() == []
        manifest = c.get("/api/v1/manifest", headers=auth(settings)).json()["files"]
        assert {"path": RIDE_PATH, "size": len(data)} in manifest
        assert put(c, settings, data).json()["ride"] is None
        bigger = ride_bytes(n=40)
        assert put(c, settings, bigger).json()["ride"] is None
        assert c.get("/api/v1/rides").json() == []


def test_delete_ride_reprocess(settings):
    with TestClient(create_app(settings)) as c:
        pk = put(c, settings, ride_bytes()).json()["ride"]
        assert c.delete(f"/api/v1/rides/{pk}").status_code == 200
    store = store_mod.Store(settings)
    try:
        store.reprocess(only_outdated=False)
        assert store.list_rides() == []
    finally:
        store.close()


def test_delete_ride_unknown_and_auth(settings):
    with TestClient(create_app(settings)) as c:
        assert c.delete("/api/v1/rides/9999").status_code == 404
    settings.web_user, settings.web_password = "fahrer", "geheim"
    with TestClient(create_app(settings)) as c:
        pk = put(c, settings, ride_bytes()).json()["ride"]
        assert c.delete(f"/api/v1/rides/{pk}").status_code == 401
        assert c.delete(f"/api/v1/rides/{pk}", auth=("fahrer", "geheim")).status_code == 200
