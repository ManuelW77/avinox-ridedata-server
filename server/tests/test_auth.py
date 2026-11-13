"""Tests für Login, Sessions, CSRF, Benutzerverwaltung und OIDC (gemockter IdP) – nur synthetische Daten."""

import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from avinox.server import auth
from avinox.server.app import create_app
from avinox.server.config import load_settings

ADMIN, ADMIN_PW = "admin", "admin-passwort-1"
XRW = {"X-Requested-With": "fetch"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("AVINOX_UPLOAD_TOKEN", "AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD", "AVINOX_DATA_DIR", "AVINOX_PUBLIC_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AVINOX_GEOCODE_URL", "off")


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path / "data")


@pytest.fixture
def login_settings(settings):
    settings.web_user, settings.web_password = ADMIN, ADMIN_PW
    return settings


def make(settings, **kw):
    return TestClient(create_app(settings), follow_redirects=False, **kw)


def csrf_of(client) -> str:
    html = client.get("/login").text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def login(client, user=ADMIN, password=ADMIN_PW, remember=False, next_url="/", csrf=None):
    data = {"username": user, "password": password, "next": next_url, "csrf": csrf or csrf_of(client)}
    if remember:
        data["remember"] = "1"
    return client.post("/login", data=data)


def logged_in(settings, **kw):
    c = make(settings, **kw)
    assert login(c).status_code == 303
    return c


# ------------------------------------------------------------ Hashing, Start-Admin, offener Modus

def test_password_hash():
    h1, h2 = auth.hash_password("geheim123"), auth.hash_password("geheim123")
    assert h1 != h2 and h1.startswith(f"scrypt${auth.SCRYPT_N}${auth.SCRYPT_R}${auth.SCRYPT_P}$")
    assert len(h1.split("$")) == 6
    assert auth.verify_password("geheim123", h1) and not auth.verify_password("geheim124", h1)
    for broken in (None, "", "scrypt$x", "md5$1$2$3$a$b", "scrypt$a$b$c$d$e"):
        assert not auth.verify_password("geheim123", broken)


def test_start_admin_created_but_never_overwritten(login_settings):
    with make(login_settings) as c:
        user = c.app.state.auth.by_name(ADMIN)
        assert user["is_admin"] == 1 and user["password_hash"] != ADMIN_PW
    login_settings.web_password = "ganz-anderes-passwort"
    with make(login_settings) as c:  # vorhandener Benutzer bleibt unverändert
        assert c.get("/api/v1/rides", auth=(ADMIN, ADMIN_PW)).status_code == 200
        assert c.get("/api/v1/rides", auth=(ADMIN, "ganz-anderes-passwort")).status_code == 401


def test_open_mode_without_users(settings):
    with make(settings) as c:
        assert c.get("/").status_code == 200
        assert c.get("/api/v1/rides").status_code == 200
        assert c.get("/login", params={"next": "/rides"}).headers["location"] == "/rides"
        assert c.post("/api/v1/users", json={"username": "x", "password": "12345678"}).status_code == 403  # keine Verwaltung ohne Login


# ------------------------------------------------------------ Login

def test_login_ok_wrong_and_cookie_flags(login_settings):
    with make(login_settings, base_url="https://testserver") as c:
        r = login(c)
        assert r.status_code == 303 and r.headers["location"] == "/"
        cookie = r.headers["set-cookie"].lower()
        assert "avinox_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
        assert "max-age" not in cookie  # ohne Häkchen: Browser-Session
        assert c.get("/").status_code == 200
    with make(login_settings) as c:  # http: kein Secure; mit Häkchen 30 Tage
        r = login(c, remember=True)
        assert "secure" not in r.headers["set-cookie"].lower() and f"max-age={30 * 86400}" in r.headers["set-cookie"].lower()
        assert c.get("/api/v1/rides").status_code == 200


def test_login_failure_is_generic(login_settings):
    with make(login_settings) as c:
        bad_pw = login(c, password="falsch-falsch")
        bad_user = login(c, user="gibtsnicht", password="falsch-falsch")
        assert bad_pw.status_code == bad_user.status_code == 401
        err = lambda r: re.search(r'class="notice login-error"[^>]*>([^<]+)<', r.text).group(1)
        assert err(bad_pw) == err(bad_user)
        assert "avinox_session" not in bad_pw.headers.get("set-cookie", "")


def test_login_throttle(login_settings):
    with make(login_settings) as c:
        for _ in range(10):
            assert login(c, password="falsch-falsch").status_code == 401
        assert login(c, password="falsch-falsch").status_code == 429
        assert login(c).status_code == 429  # auch das richtige Passwort ist für diese IP+Benutzer gesperrt
        c.app.state.auth.create_user("anna", "anna-passwort")
        assert login(c, user="anna", password="anna-passwort").status_code == 303  # anderer Benutzer nicht betroffen


def test_throttle_window_expires():
    t = auth.LoginThrottle(limit=2, window=0.05)
    t.fail("1.2.3.4", "A"); t.fail("1.2.3.4", "a")
    assert t.blocked("1.2.3.4", "a") and not t.blocked("5.6.7.8", "a")
    time.sleep(0.06)
    assert not t.blocked("1.2.3.4", "a")


def test_login_needs_csrf_token(login_settings):
    with make(login_settings) as c:
        csrf_of(c)
        assert c.post("/login", data={"username": ADMIN, "password": ADMIN_PW}).status_code == 403
        assert login(c, csrf="falsch").status_code == 403
        assert login(c, csrf=csrf_of(c)).status_code == 303
    with make(login_settings) as c:
        token = csrf_of(c)
        r = c.post("/login", data={"username": ADMIN, "password": ADMIN_PW, "csrf": token}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403


def test_next_redirect_and_open_redirect_guard(login_settings):
    with make(login_settings) as c:
        assert login(c, next_url="/rides?x=1").headers["location"] == "/rides?x=1"
    for evil in ("//evil.example", "https://evil.example/", "/\\evil.example", "javascript:alert(1)", "", "rides"):
        with make(login_settings) as c:
            assert login(c, next_url=evil).headers["location"] == "/", evil
    with make(login_settings) as c:
        assert c.get("/login", params={"next": "//evil.example"}).status_code == 200
        assert 'name="next" value="/"' in c.get("/login", params={"next": "//evil.example"}).text


def test_html_redirects_and_api_401(login_settings):
    with make(login_settings) as c:
        r = c.get("/rides")
        assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Frides"
        assert c.get("/settings").status_code == 303
        r = c.get("/api/v1/rides")
        assert r.status_code == 401 and r.json()["detail"] and r.headers["content-type"].startswith("application/json")
        assert c.get("/healthz").status_code == 200 and c.get("/login").status_code == 200
        assert c.get("/static/app.css").status_code == 200


def test_basic_auth_fallback_for_api(login_settings):
    with make(login_settings) as c:
        assert c.get("/api/v1/rides", auth=(ADMIN, ADMIN_PW)).status_code == 200
        assert c.get("/api/v1/rides", auth=(ADMIN, "falsch")).status_code == 401
        assert c.get("/api/v1/rides", auth=("niemand", ADMIN_PW)).status_code == 401
        # Basic ohne Origin (Skript) geht durch, mit fremder Origin (Browser) nicht
        assert c.delete("/api/v1/rides/9999", auth=(ADMIN, ADMIN_PW)).status_code == 404
        assert c.delete("/api/v1/rides/9999", auth=(ADMIN, ADMIN_PW), headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.delete("/api/v1/rides/9999", auth=(ADMIN, ADMIN_PW), headers={"Origin": "http://testserver"}).status_code == 404
        # OIDC-Benutzer ohne Passwort können sich nicht per Basic anmelden
        c.app.state.auth.create_user("sso-user", None, True, oidc_issuer="x", oidc_sub="1")
        assert c.get("/api/v1/rides", auth=("sso-user", "")).status_code == 401


def test_basic_auth_is_throttled(login_settings):
    with make(login_settings) as c:
        for _ in range(auth.FAIL_LIMIT):
            assert c.get("/api/v1/rides", auth=(ADMIN, "falsch")).status_code == 401
        # Gesperrt – auch mit richtigem Passwort, solange das Fenster läuft
        assert c.get("/api/v1/rides", auth=(ADMIN, ADMIN_PW)).status_code == 429


def test_upload_bearer_unchanged(login_settings):
    with make(login_settings) as c:
        h = {"Authorization": f"Bearer {login_settings.upload_token}"}
        assert c.get("/api/v1/manifest", headers=h).status_code == 200
        assert c.get("/api/v1/manifest").status_code == 401


# ------------------------------------------------------------ CSRF, Logout, Sessions

def test_csrf_origin_checks_for_session(login_settings):
    c = logged_in(login_settings)
    put = lambda **h: c.put("/api/v1/settings/modes", json={}, headers=h)
    assert put(Origin="https://evil.example").status_code == 403
    assert put(Referer="https://evil.example/x").status_code == 403
    assert put(Origin="null").status_code == 403
    assert put(Origin="http://testserver").status_code == 200
    assert put(Referer="http://testserver/settings").status_code == 200
    assert put().status_code == 403  # weder Origin noch Referer noch X-Requested-With
    assert put(**XRW).status_code == 200
    assert c.get("/api/v1/rides", headers={"Origin": "https://evil.example"}).status_code == 200  # GET ändert nichts


def test_csrf_public_url_host_allowed(login_settings):
    login_settings.public_url = "https://avinox.example.test"
    c = logged_in(login_settings)
    assert c.put("/api/v1/settings/modes", json={}, headers={"Origin": "https://avinox.example.test"}).status_code == 200


def test_logout_is_post_only_with_token(login_settings):
    c = logged_in(login_settings)
    assert c.get("/logout").status_code == 405
    assert c.post("/logout").status_code == 403
    token = re.search(r'action="/logout"[^>]*><input type="hidden" name="csrf" value="([^"]+)"', c.get("/settings").text).group(1)
    r = c.post("/logout", data={"csrf": token}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.get("/api/v1/rides").status_code == 200
    r = c.post("/logout", data={"csrf": token})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/v1/rides").status_code == 401
    assert c.app.state.auth.db.one("SELECT COUNT(*) AS n FROM sessions")["n"] == 0


def test_session_stored_hashed_expires_and_survives_restart(login_settings):
    c = logged_in(login_settings)
    token = c.cookies.get("avinox_session")
    db = c.app.state.auth.db
    row = db.one("SELECT * FROM sessions")
    assert row["token_hash"] != token and len(row["token_hash"]) == 64 and token not in str(dict(row))
    c.close()
    with make(login_settings) as c2:  # Neustart: gültige Sessions bleiben
        c2.cookies.set("avinox_session", token)
        assert c2.get("/api/v1/rides").status_code == 200
        c2.app.state.auth.db.execute("UPDATE sessions SET expires = '2000-01-01T00:00:00+00:00'")
        assert c2.get("/api/v1/rides").status_code == 401
        c2.app.state.auth.purge_expired()
        assert c2.app.state.auth.db.one("SELECT COUNT(*) AS n FROM sessions")["n"] == 0


def test_session_lifetimes(login_settings):
    c = make(login_settings)
    store = c.app.state.auth
    uid = store.by_name(ADMIN)["id"]
    _, short = store.create_session(uid, False)
    _, long = store.create_session(uid, True)
    assert short is None and long == 30 * 86400
    rows = store.db.query("SELECT created, expires FROM sessions ORDER BY expires")
    from datetime import datetime
    hours = [(datetime.fromisoformat(r["expires"]) - datetime.fromisoformat(r["created"])).total_seconds() / 3600 for r in rows]
    assert round(hours[0]) == 12 and round(hours[1]) == 720


def test_password_change_ends_other_sessions(login_settings):
    c1, c2 = logged_in(login_settings), None
    c2 = TestClient(c1.app, follow_redirects=False)
    assert login(c2).status_code == 303
    assert c2.get("/api/v1/rides").status_code == 200
    body = {"old_password": ADMIN_PW, "new_password": "neues-passwort-2"}
    assert c1.put("/api/v1/users/me/password", json={**body, "old_password": "falsch"}, headers=XRW).status_code == 400
    assert c1.put("/api/v1/users/me/password", json={**body, "new_password": "kurz"}, headers=XRW).status_code == 400
    assert c1.put("/api/v1/users/me/password", json=body, headers=XRW).status_code == 200
    assert c1.get("/api/v1/rides").status_code == 200  # die ändernde Sitzung bleibt
    assert c2.get("/api/v1/rides").status_code == 401  # die andere ist beendet
    assert c1.get("/api/v1/rides", auth=(ADMIN, ADMIN_PW)).status_code == 200  # Session gilt weiter, Basic mit altem PW nicht:
    c3 = TestClient(c1.app, follow_redirects=False)
    assert c3.get("/api/v1/rides", auth=(ADMIN, ADMIN_PW)).status_code == 401
    assert c3.get("/api/v1/rides", auth=(ADMIN, "neues-passwort-2")).status_code == 200


# ------------------------------------------------------------ Benutzerverwaltung, Admin-Rechte

def test_user_management_and_admin_rights(login_settings):
    admin = logged_in(login_settings)
    r = admin.post("/api/v1/users", json={"username": "Anna", "password": "anna-passwort"}, headers=XRW)
    assert r.status_code == 200 and r.json()["user"]["is_admin"] is False
    assert admin.post("/api/v1/users", json={"username": "anna", "password": "anna-passwort"}, headers=XRW).status_code == 409  # Name ohne Groß/Klein
    assert admin.post("/api/v1/users", json={"username": "x y", "password": "anna-passwort"}, headers=XRW).status_code == 400
    assert admin.post("/api/v1/users", json={"username": "bert", "password": "kurz"}, headers=XRW).status_code == 400
    users = admin.get("/api/v1/users").json()["users"]
    assert {u["username"] for u in users} == {ADMIN, "Anna"} and all("password_hash" not in u for u in users)
    anna_id = r.json()["user"]["id"]

    anna = TestClient(admin.app, follow_redirects=False)
    assert login(anna, "anna", "anna-passwort").status_code == 303
    assert anna.get("/api/v1/rides").status_code == 200
    assert anna.get("/api/v1/users").status_code == 403
    assert anna.post("/api/v1/users", json={"username": "evil", "password": "evil-passwort"}, headers=XRW).status_code == 403
    assert anna.delete(f"/api/v1/users/{anna_id}", headers=XRW).status_code == 403
    assert anna.post("/api/v1/firmware", files={"file": ("a.bin", b"x")}, headers=XRW).status_code == 403
    assert anna.get("/settings").status_code == 200 and "user-add-form" not in anna.get("/settings").text

    assert admin.put(f"/api/v1/users/{anna_id}/password", json={"password": "kurz"}, headers=XRW).status_code == 400
    assert admin.put(f"/api/v1/users/{anna_id}/password", json={"password": "anna-neu-1234"}, headers=XRW).status_code == 200
    assert anna.get("/api/v1/rides").status_code == 401  # Sessions des Benutzers beendet
    assert login(TestClient(admin.app, follow_redirects=False), "anna", "anna-passwort").status_code == 401

    admin_id = admin.get("/api/v1/users/me").json()["user"]["id"]
    assert admin.delete(f"/api/v1/users/{admin_id}", headers=XRW).status_code == 409  # nicht sich selbst
    assert admin.delete("/api/v1/users/9999", headers=XRW).status_code == 404
    assert admin.delete(f"/api/v1/users/{anna_id}", headers=XRW).status_code == 200
    assert admin.app.state.auth.by_name("Anna") is None


def test_last_admin_cannot_be_deleted(login_settings):
    admin = logged_in(login_settings)
    store = admin.app.state.auth
    second = store.create_user("zweit", "zweit-passwort", True)
    me = store.by_name(ADMIN)["id"]
    other = TestClient(admin.app, follow_redirects=False)
    assert login(other, "zweit", "zweit-passwort").status_code == 303
    assert other.delete(f"/api/v1/users/{me}", headers=XRW).status_code == 200  # zwei Admins: einer darf gehen
    assert store.admin_count() == 1 and store.get_user(second["id"])
    assert other.delete(f"/api/v1/users/{second['id']}", headers=XRW).status_code == 409


def test_settings_page_card(login_settings):
    c = logged_in(login_settings)
    html = c.get("/settings").text
    assert 'id="sec-auth"' in html and 'id="pw-form"' in html and 'id="user-add-form"' in html and 'action="/logout"' in html
    assert "Anmeldung mit" not in html and 'id="oidc-form"' in html  # Status nur, wenn eingerichtet; Formular für Admins
    assert "ohne Passwort im WLAN offen" not in html
    assert 'action="/logout"' in c.get("/rides").text  # Abmelden-Knopf in der Navigation


def test_firmware_admin_only(login_settings):
    admin = logged_in(login_settings)
    admin.app.state.auth.create_user("fahrer", "fahrer-passwort")
    c = TestClient(admin.app, follow_redirects=False)
    assert login(c, "fahrer", "fahrer-passwort").status_code == 303
    assert c.post("/api/v1/firmware/1/current", headers=XRW).status_code == 403
    assert c.post("/api/v1/firmware/1/current", auth=("fahrer", "fahrer-passwort")).status_code == 403
    assert admin.post("/api/v1/firmware/1/current", headers=XRW).status_code == 404  # Admin darf (Firmware gibt es nicht)


# ------------------------------------------------------------ OIDC

ISSUER = "https://idp.example.test"


class FakeIdp:
    """Gemockter OIDC-Anbieter: Discovery, JWKS (selbst erzeugter RSA-Schlüssel) und Token-Endpunkt."""

    def __init__(self):
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})
        self.claims = {"sub": "sub-123", "email": "anna@example.test", "preferred_username": "anna", "groups": ["radler"]}
        self.nonce: str | None = None
        self.down = False
        self.token_requests: list[dict] = []
        self.override: dict = {}
        self.sign_key: RSAKey | None = None

    def id_token(self, **over) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": "avinox-test", "iat": now, "exp": now + 300, "nonce": self.nonce, **self.claims, **self.override, **over}
        return jwt.encode({"alg": "RS256", "kid": "k1"}, {k: v for k, v in claims.items() if v is not None}, self.sign_key or self.key)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            raise httpx2.ConnectError("idp down")
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx2.Response(200, json={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize", "token_endpoint": f"{ISSUER}/token",
                                              "jwks_uri": f"{ISSUER}/jwks", "token_endpoint_auth_methods_supported": ["client_secret_post"]})
        if url == f"{ISSUER}/jwks":
            return httpx2.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url == f"{ISSUER}/token":
            self.token_requests.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return httpx2.Response(200, json={"access_token": "at", "token_type": "Bearer", "expires_in": 300, "id_token": self.id_token()})
        return httpx2.Response(404)


@pytest.fixture
def idp():
    return FakeIdp()


@pytest.fixture
def oidc_settings(login_settings):
    """OIDC per Umgebung (Vorrang) plus Passwort-Admin: OIDC allein schaltet den Login nie ein."""
    settings = login_settings
    settings.oidc_issuer, settings.oidc_client_id, settings.oidc_client_secret = ISSUER, "avinox-test", "test-secret"
    settings.oidc_name = "Authentik"
    return settings


def oidc_client(settings, idp):
    c = make(settings)
    c.app.state.oidc.transport = httpx2.MockTransport(idp)
    return c


def start_oidc(c, idp, next_url="/rides"):
    """Anmeldung starten; Rückgabe: Query des Weiterleitungsziels beim IdP (state, nonce, PKCE …)."""
    r = c.get("/auth/oidc/login", params={"next": next_url})
    assert r.status_code == 303 and r.headers["location"].startswith(f"{ISSUER}/authorize?")
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    idp.nonce = q["nonce"]
    return q


def test_oidc_alone_does_not_enable_login(settings, idp):
    settings.oidc_issuer, settings.oidc_client_id = ISSUER, "avinox-test"
    with oidc_client(settings, idp) as c:
        assert c.get("/rides").status_code == 200
        assert c.get("/auth/oidc/login").status_code == 404


def test_oidc_configured_shows_button(oidc_settings, idp):
    with oidc_client(oidc_settings, idp) as c:
        assert c.get("/rides").status_code == 303
        html = c.get("/login").text
        assert "Mit Authentik anmelden" in html and "/auth/oidc/login" in html


def test_no_oidc_button_when_unconfigured(login_settings):
    with make(login_settings) as c:
        assert "/auth/oidc/login" not in c.get("/login").text
        assert c.get("/auth/oidc/login").status_code == 404


def test_oidc_full_flow(oidc_settings, idp):
    with oidc_client(oidc_settings, idp) as c:
        q = start_oidc(c, idp)
        assert q["response_type"] == "code" and q["client_id"] == "avinox-test" and q["code_challenge_method"] == "S256"
        assert q["redirect_uri"] == "http://testserver/auth/oidc/callback" and "openid" in q["scope"]
        r = c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]})
        assert r.status_code == 303 and r.headers["location"] == "/rides"
        assert c.get("/api/v1/rides").status_code == 200
        tok = idp.token_requests[0]
        assert tok["grant_type"] == "authorization_code" and tok["code"] == "abc" and tok["client_secret"] == "test-secret"
        import base64, hashlib
        challenge = base64.urlsafe_b64encode(hashlib.sha256(tok["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        assert challenge == q["code_challenge"]
        user = c.app.state.auth.by_oidc(ISSUER, "sub-123")
        assert user["username"] == "anna" and user["email"] == "anna@example.test" and user["is_admin"] == 1 and user["password_hash"] is None
        # zweite Anmeldung: derselbe Benutzer
        c2 = oidc_client(oidc_settings, idp)
        q = start_oidc(c2, idp)
        assert c2.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).status_code == 303
        assert c.app.state.auth.user_count() == 2


def test_oidc_redirect_uri_uses_public_url(oidc_settings, idp):
    oidc_settings.public_url = "https://avinox.example.test"
    with oidc_client(oidc_settings, idp) as c:
        assert start_oidc(c, idp)["redirect_uri"] == "https://avinox.example.test/auth/oidc/callback"


def test_oidc_state_mismatch_rejected(oidc_settings, idp):
    with oidc_client(oidc_settings, idp) as c:
        q = start_oidc(c, idp)
        r = c.get("/auth/oidc/callback", params={"code": "abc", "state": "falscher-state"})
        assert r.status_code == 303 and "error=state" in r.headers["location"]
        assert c.get("/api/v1/rides").status_code == 401 and not idp.token_requests
        # state stimmt, aber der Browser hat das Cookie nicht (Anmeldung in anderem Browser gestartet)
        other = oidc_client(oidc_settings, idp)
        r = other.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]})
        assert "error=state" in r.headers["location"] and not idp.token_requests
        # State ist nur einmal gültig
        assert c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).status_code == 303
        assert c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).headers["location"].endswith("error=state")


def test_oidc_bad_id_tokens_rejected(oidc_settings, idp):
    for override in ({"nonce": "anderer-nonce"}, {"aud": "anderer-client"}, {"iss": "https://evil.example"}, {"exp": int(time.time()) - 10}):
        idp.override = override
        with oidc_client(oidc_settings, idp) as c:
            q = start_oidc(c, idp)
            r = c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]})
            assert "error=failed" in r.headers["location"], override
            assert c.get("/api/v1/rides").status_code == 401
    idp.override = {}
    idp.sign_key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig"})  # JWKS kennt nur den echten Schlüssel
    with oidc_client(oidc_settings, idp) as c:
        q = start_oidc(c, idp)
        r = c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]})
        assert "error=failed" in r.headers["location"] and c.get("/api/v1/rides").status_code == 401


def test_oidc_idp_errors_are_readable_not_500(oidc_settings, idp):
    with oidc_client(oidc_settings, idp) as c:
        idp.down = True
        r = c.get("/auth/oidc/login")
        assert r.status_code == 303 and r.headers["location"].startswith("/login?error=unreachable")
        page = c.get(r.headers["location"])
        assert page.status_code == 200 and "nicht erreichbar" in page.text
        idp.down = False
        q = start_oidc(c, idp)
        idp.down = True  # IdP fällt zwischen Start und Rückkehr aus
        r = c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]})
        assert r.status_code == 303 and "error=" in r.headers["location"]
        # Abbruch beim IdP (z. B. Zugriff verweigert)
        idp.down = False
        q = start_oidc(c, idp)
        r = c.get("/auth/oidc/callback", params={"error": "access_denied", "state": q["state"]})
        assert "error=denied" in r.headers["location"]
        assert "nicht erlaubt" in c.get("/login", params={"error": "denied"}).text


def test_oidc_allowed_list(oidc_settings, idp):
    oidc_settings.oidc_allowed = "chef@example.test, Familie"
    with oidc_client(oidc_settings, idp) as c:  # weder E-Mail noch Gruppe passen
        q = start_oidc(c, idp)
        r = c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]})
        assert "error=denied" in r.headers["location"] and c.app.state.auth.user_count() == 1
    idp.claims["groups"] = ["familie"]  # Gruppe (ohne Beachtung der Schreibweise)
    with oidc_client(oidc_settings, idp) as c:
        q = start_oidc(c, idp)
        assert c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).headers["location"] == "/rides"
    idp.claims.update(groups=[], email="chef@example.test", sub="sub-8", preferred_username="chef0")
    with oidc_client(oidc_settings, idp) as c:  # E-Mail ohne email_verified zählt nicht
        q = start_oidc(c, idp)
        assert "error=denied" in c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).headers["location"]
    idp.claims.update(email_verified=True, sub="sub-9", preferred_username="chef")
    with oidc_client(oidc_settings, idp) as c:  # bestätigte E-Mail passt
        q = start_oidc(c, idp)
        assert c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).headers["location"] == "/rides"
    idp.claims.update(email_verified=False, sub="sub-10", preferred_username="x")  # unbestätigte E-Mail zählt nicht
    with oidc_client(oidc_settings, idp) as c:
        q = start_oidc(c, idp)
        assert "error=denied" in c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).headers["location"]


def test_oidc_admin_setting_and_name_conflict(oidc_settings, idp):
    oidc_settings.web_user, oidc_settings.web_password = "anna", "anna-passwort-1"  # Passwortbenutzer mit gleichem Namen
    oidc_settings.oidc_admin = "chef@example.test"
    with oidc_client(oidc_settings, idp) as c:
        q = start_oidc(c, idp)
        assert c.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]}).status_code == 303
        store = c.app.state.auth
        sso = store.by_oidc(ISSUER, "sub-123")
        assert sso["username"] == "anna-2" and sso["is_admin"] == 0  # nicht mit dem Passwortbenutzer verknüpft
        assert store.by_name("anna")["oidc_sub"] is None and store.by_name("anna")["is_admin"] == 1
        assert c.get("/api/v1/users").status_code == 403  # kein Admin
        assert c.get("/api/v1/rides").status_code == 200


# ------------------------------------------------------------ Anmeldung ein- und ausschalten
SETUP = {"username": "erster", "password": "erster-passwort-1"}


def enable(c, body=None, headers=XRW):
    return c.post("/api/v1/auth/enable", json=body or SETUP, headers=headers)


def test_login_off_by_default(settings):
    c = make(settings)
    assert c.get("/settings").status_code == 200
    html = c.get("/settings").text
    assert 'id="enable-form"' in html and 'id="su-pass2"' in html and "su-code" not in html
    assert c.get("/login", params={"next": "/rides"}).headers["location"] == "/rides"
    assert not hasattr(c.app.state, "setup_code")


def test_enable_creates_admin_and_logs_in(settings):
    c = make(settings)
    r = enable(c)
    assert r.status_code == 200 and r.json() == {"ok": True} and "avinox_session=" in r.headers["set-cookie"]
    assert c.app.state.auth.by_name("erster")["is_admin"] == 1
    assert c.get("/settings").status_code == 200
    assert 'id="enable-form"' not in c.get("/settings").text and 'id="oidc-form"' in c.get("/settings").text
    assert make(settings).get("/settings").status_code == 303
    assert enable(c).status_code == 409  # schon an


def test_enable_validates_new_admin(settings):
    c = make(settings)
    assert enable(c, {"username": "erster", "password": "kurz"}).status_code == 400
    assert enable(c, {"username": "ungültig name!", "password": "erster-passwort-1"}).status_code == 400
    assert c.app.state.auth.user_count() == 0


def test_enable_csrf(settings):
    c = make(settings)
    assert enable(c, headers={"Origin": "http://evil.example"}).status_code == 403
    assert enable(c, headers={}).status_code == 403
    assert c.app.state.auth.user_count() == 0
    assert enable(c, headers={"Origin": "http://testserver"}).status_code == 200


def test_disable_admin_only_then_open(settings):
    admin = make(settings)
    assert enable(admin).status_code == 200
    admin.app.state.auth.create_user("fahrer", "fahrer-passwort")
    other = TestClient(admin.app, follow_redirects=False)
    assert login(other, "fahrer", "fahrer-passwort").status_code == 303
    assert other.post("/api/v1/auth/disable", headers=XRW).status_code == 403
    assert TestClient(admin.app).post("/api/v1/auth/disable", headers=XRW).status_code == 401  # nicht angemeldet
    assert admin.post("/api/v1/auth/disable", headers=XRW).status_code == 200
    anon = TestClient(admin.app, follow_redirects=False)
    assert anon.get("/settings").status_code == 200 and anon.get("/api/v1/rides").status_code == 200
    assert admin.app.state.auth.user_count() == 2  # Benutzer bleiben
    html = anon.get("/settings").text
    assert 'id="enable-form"' in html and "bestehenden Admin" in html and 'id="su-pass2"' not in html


def test_reenable_needs_existing_admin(settings):
    admin = make(settings)
    enable(admin)
    admin.app.state.auth.create_user("fahrer", "fahrer-passwort")
    assert admin.post("/api/v1/auth/disable", headers=XRW).status_code == 200
    anon = TestClient(admin.app, follow_redirects=False)
    r = enable(anon, {"username": "erster", "password": "falsch-falsch"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "bad_login"
    assert enable(anon, {"username": "neu", "password": "neu-passwort-1"}).status_code == 403  # unbekannt: nichts wird angelegt
    assert anon.app.state.auth.by_name("neu") is None
    assert enable(anon, {"username": "fahrer", "password": "fahrer-passwort"}).status_code == 403  # kein Admin
    assert anon.get("/settings").status_code == 200
    r = enable(anon)
    assert r.status_code == 200 and "avinox_session=" in r.headers["set-cookie"]
    assert anon.get("/settings").status_code == 200 and make(settings).get("/settings").status_code == 303


def test_enable_is_throttled(settings):
    c = make(settings)
    enable(c)
    c.post("/api/v1/auth/disable", headers=XRW)
    anon = TestClient(c.app, follow_redirects=False)
    for _ in range(10):
        assert enable(anon, {"username": "erster", "password": "falsch-falsch"}).status_code == 403
    assert enable(anon, {"username": "erster", "password": "falsch-falsch"}).status_code == 429
    assert enable(anon).status_code == 429


def test_env_admin_login_on_and_not_disableable(login_settings):
    c = logged_in(login_settings)
    r = c.post("/api/v1/auth/disable", headers=XRW)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "env_locked"
    assert c.get("/api/v1/rides").status_code == 200 and make(login_settings).get("/").status_code == 303
    html = c.get("/settings").text
    assert "AVINOX_WEB_USER" in html and 'id="auth-disable"' not in html


def test_env_overrides_disabled_flag(login_settings):
    with make(login_settings) as c:
        c.app.state.auth.db.set_setting("auth_enabled", False)
    assert make(login_settings).get("/").status_code == 303  # Neustart schaltet per Env wieder ein


def test_migration_existing_users_without_key(settings):
    with make(settings) as c:
        c.app.state.auth.create_user("alt", "alt-passwort-1", True)
        assert c.get("/").status_code == 200  # Schlüssel fehlt, gesetzt wird er erst beim Start
    assert make(settings).get("/").status_code == 303


def test_existing_users_with_flag_off_stay_open(settings):
    with make(settings) as c:
        c.app.state.auth.create_user("alt", "alt-passwort-1", True)
        c.app.state.auth.db.set_setting("auth_enabled", False)
    assert make(settings).get("/").status_code == 200


def test_disable_button_in_settings(settings):
    c = make(settings)
    enable(c)
    html = c.get("/settings").text
    assert 'id="auth-disable"' in html and 'id="oidc-form"' in html
    assert "http://testserver/auth/oidc/callback" in html


# ------------------------------------------------------------ OIDC in den Einstellungen
OIDC_BODY = {"enabled": True, "issuer": ISSUER + "/", "client_id": "avinox-test", "secret": "geheim-secret", "name": "Authentik", "allowed": "familie", "admin": ""}


def admin_client(settings, idp=None):
    c = make(settings)
    enable(c)
    if idp:
        c.app.state.oidc.transport = httpx2.MockTransport(idp)
    return c


def test_oidc_settings_secret_never_returned(settings):
    c = admin_client(settings)
    r = c.put("/api/v1/settings/oidc", json=OIDC_BODY, headers=XRW)
    assert r.status_code == 200
    body = r.json()
    assert body["has_secret"] is True and "geheim-secret" not in r.text and "secret" not in body
    assert body["issuer"] == ISSUER and body["redirect_uri"] == "http://testserver/auth/oidc/callback"
    got = c.get("/api/v1/settings/oidc")
    assert got.json() == body and "geheim-secret" not in got.text
    assert "geheim-secret" not in c.get("/settings").text
    assert c.app.state.oidc.config().client_secret == "geheim-secret" and c.app.state.oidc.enabled


def test_oidc_settings_empty_secret_keeps_old(settings):
    c = admin_client(settings)
    c.put("/api/v1/settings/oidc", json=OIDC_BODY, headers=XRW)
    r = c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "secret": "", "name": "Neu"}, headers=XRW)
    assert r.json()["has_secret"] is True and r.json()["name"] == "Neu"
    assert c.app.state.oidc.config().client_secret == "geheim-secret"
    c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "secret": "anderes"}, headers=XRW)
    assert c.app.state.oidc.config().client_secret == "anderes"


def test_oidc_settings_secret_dropped_when_issuer_or_client_changes(settings):
    c = admin_client(settings)
    c.put("/api/v1/settings/oidc", json=OIDC_BODY, headers=XRW)
    r = c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "secret": "", "issuer": "https://anderer.example.test"}, headers=XRW)
    assert r.json()["has_secret"] is False and c.app.state.oidc.config().client_secret == ""
    c.put("/api/v1/settings/oidc", json=OIDC_BODY, headers=XRW)
    r = c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "secret": "", "client_id": "andere-id"}, headers=XRW)
    assert r.json()["has_secret"] is False


def test_oidc_settings_validation(settings):
    c = admin_client(settings)
    for issuer in ("http://idp.example.test", "ftp://idp.example.test", "idp.example.test", "https://u:p@idp.example.test", "https://idp.example.test/?x=1"):
        r = c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "issuer": issuer}, headers=XRW)
        assert r.status_code == 400 and r.json()["detail"]["error"] == "issuer_invalid", issuer
    for issuer in ("http://localhost:9000", "http://192.168.1.5/realm", "http://127.0.0.1:8080"):
        assert c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "issuer": issuer}, headers=XRW).status_code == 200, issuer
    r = c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "client_id": ""}, headers=XRW)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "oidc_incomplete"
    assert c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "enabled": False, "issuer": "", "client_id": ""}, headers=XRW).status_code == 200


def test_oidc_settings_admin_only(settings):
    admin = admin_client(settings)
    admin.app.state.auth.create_user("fahrer", "fahrer-passwort")
    c = TestClient(admin.app, follow_redirects=False)
    assert login(c, "fahrer", "fahrer-passwort").status_code == 303
    assert c.get("/api/v1/settings/oidc").status_code == 403
    assert c.put("/api/v1/settings/oidc", json=OIDC_BODY, headers=XRW).status_code == 403
    assert c.post("/api/v1/settings/oidc/test", headers=XRW).status_code == 403
    assert 'id="oidc-form"' not in c.get("/settings").text
    assert TestClient(admin.app).get("/api/v1/settings/oidc").status_code == 401  # nicht angemeldet
    assert make(load_settings(settings.data_dir.parent / "zweit")).get("/api/v1/settings/oidc").status_code == 403  # Login aus


def test_oidc_settings_env_wins(oidc_settings):
    c = logged_in(oidc_settings)
    r = c.get("/api/v1/settings/oidc")
    assert r.json()["env_locked"] is True and r.json()["issuer"] == ISSUER and r.json()["has_secret"] is True and "test-secret" not in r.text
    r = c.put("/api/v1/settings/oidc", json=OIDC_BODY, headers=XRW)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "env_locked"
    html = c.get("/settings").text
    assert "readonly" in html and "test-secret" not in html


def test_oidc_settings_test_button(settings, idp):
    c = admin_client(settings, idp)
    assert c.post("/api/v1/settings/oidc/test", headers=XRW).json() == {"ok": False, "error": "not_configured"}
    c.put("/api/v1/settings/oidc", json=OIDC_BODY, headers=XRW)
    assert c.post("/api/v1/settings/oidc/test", headers=XRW).json() == {"ok": True}
    idp.down = True
    r = c.post("/api/v1/settings/oidc/test", headers=XRW)
    assert r.json() == {"ok": False, "error": "unreachable"} and "idp.example.test" not in r.text


def test_oidc_from_db_full_flow(settings, idp):
    c = admin_client(settings, idp)
    c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "allowed": ""}, headers=XRW)
    anon = TestClient(c.app, follow_redirects=False)
    assert "Mit Authentik anmelden" in anon.get("/login").text
    q = start_oidc(anon, idp)
    assert q["client_id"] == "avinox-test"
    r = anon.get("/auth/oidc/callback", params={"code": "abc", "state": q["state"]})
    assert r.status_code == 303 and r.headers["location"] == "/rides"
    assert idp.token_requests[0]["client_secret"] == "geheim-secret"
    assert anon.get("/api/v1/rides").status_code == 200
    c.put("/api/v1/settings/oidc", json={**OIDC_BODY, "enabled": False}, headers=XRW)  # aus: kein Knopf, kein Einstieg
    assert "/auth/oidc/login" not in TestClient(c.app).get("/login").text
    assert TestClient(c.app).get("/auth/oidc/login").status_code == 404
