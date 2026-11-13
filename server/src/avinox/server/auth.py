"""Anmeldung: Passwort-Hashing, Benutzer, serverseitige Sessions, Login-Bremse und OIDC.

Der Login ist aktiv, wenn er in den Einstellungen eingeschaltet ist (``auth_enabled``) und es
mindestens einen Benutzer gibt; sonst bleibt die Seite wie bisher im LAN offen. Alles nutzt nur
die Standardbibliothek plus ``authlib``/``joserfc``/``httpx2`` für OIDC.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from .config import Settings
from .db import Database

COOKIE = "avinox_session"
CSRF_COOKIE = "avinox_csrf"
OIDC_COOKIE = "avinox_oidc"
REMEMBER_DAYS = 30
SESSION_HOURS = 12
MIN_PASSWORD = 8
MAX_PASSWORD = 512
USERNAME_RE = re.compile(r"^[\w.@+-]{1,64}$")

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
FAIL_LIMIT, FAIL_WINDOW = 10, 15 * 60


# ------------------------------------------------------------ Passwörter

def hash_password(password: str, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    """``scrypt$n$r$p$salt$hash`` (Salt und Hash base64), zufälliger Salt je Aufruf."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * n * r * 2)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        n, r, p = int(n), int(r), int(p)
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=n, r=r, p=p, dklen=len(expected), maxmem=128 * n * r * 2)
    except (ValueError, TypeError):
        return False
    return scheme == "scrypt" and secrets.compare_digest(got, expected)


_DUMMY_HASH = hash_password("dummy")
"""Gegen unbekannte Benutzer prüfen, damit die Antwortzeit nichts verrät."""


def password_problem(password: str) -> str | None:
    """Fehlercode für ein unzulässiges Passwort, sonst ``None``."""
    if len(password) < MIN_PASSWORD:
        return "password_short"
    if len(password) > MAX_PASSWORD:
        return "password_long"
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def safe_next(value: str | None) -> str:
    """Nur relative Pfade (``/…``, nicht ``//…`` oder ``/\\…``) – sonst Startseite (kein Open Redirect)."""
    if value and value.startswith("/") and not value.startswith(("//", "/\\")) and "\n" not in value and "\r" not in value:
        return value
    return "/"


# ------------------------------------------------------------ Benutzer und Sessions

class AuthStore:
    """Benutzer und Sessions in SQLite (Tabellen ``users`` und ``sessions``)."""

    def __init__(self, db: Database):
        self.db = db
        secret = db.get_setting("auth_secret")
        if not secret:
            secret = secrets.token_hex(32)
            db.set_setting("auth_secret", secret)
        self._secret = secret.encode()
        self.purge_expired()

    # --- Benutzer
    def user_count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM users")["n"]

    def admin_count(self) -> int:
        return self.db.one("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1")["n"]

    def get_user(self, user_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        return dict(row) if row else None

    def by_name(self, username: str) -> dict | None:
        row = self.db.one("SELECT * FROM users WHERE username = ?", (username,))
        return dict(row) if row else None

    def by_oidc(self, issuer: str, sub: str) -> dict | None:
        row = self.db.one("SELECT * FROM users WHERE oidc_issuer = ? AND oidc_sub = ?", (issuer, sub))
        return dict(row) if row else None

    def list_users(self) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT id, username, email, is_admin, created, last_login, (password_hash IS NOT NULL) AS has_password, "
            "(oidc_sub IS NOT NULL) AS is_oidc FROM users ORDER BY username")]

    def create_user(self, username: str, password: str | None = None, is_admin: bool = False, *, email: str | None = None,
                    oidc_issuer: str | None = None, oidc_sub: str | None = None) -> dict:
        cur = self.db.execute(
            "INSERT INTO users(username, password_hash, oidc_sub, oidc_issuer, email, is_admin, created) VALUES (?,?,?,?,?,?,?)",
            (username, hash_password(password) if password else None, oidc_sub, oidc_issuer, email, int(is_admin), _now()))
        return self.get_user(cur.lastrowid)  # type: ignore[return-value]

    def ensure_admin(self, username: str, password: str) -> None:
        """Start-Admin aus der Umgebung: nur anlegen, wenn es den Benutzer noch nicht gibt."""
        if not self.by_name(username):
            self.create_user(username, password, True)

    def delete_user(self, user_id: int) -> None:
        self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))  # Sessions folgen per ON DELETE CASCADE

    def set_password(self, user_id: int, password: str) -> None:
        self.db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))

    def touch_login(self, user_id: int) -> None:
        self.db.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), user_id))

    def check_password(self, username: str, password: str) -> dict | None:
        """Passwortbenutzer prüfen (zeitlich gleich, ob es den Benutzer gibt oder nicht)."""
        user = self.by_name(username)
        ok = verify_password(password, user["password_hash"] if user and user["password_hash"] else _DUMMY_HASH)
        return user if user and user["password_hash"] and ok else None

    # --- Sessions
    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, user_id: int, remember: bool) -> tuple[str, int | None]:
        """Neue Session: (Token, Cookie-Lebensdauer in s oder ``None`` = Browser-Session)."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        life = timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS)
        self.db.execute(
            "INSERT INTO sessions(token_hash, user_id, created, expires, last_seen) VALUES (?,?,?,?,?)",
            (self._hash(token), user_id, now.isoformat(timespec="seconds"), (now + life).isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
        self.touch_login(user_id)
        return token, int(life.total_seconds()) if remember else None

    def session_user(self, token: str | None) -> dict | None:
        if not token:
            return None
        row = self.db.one(
            "SELECT u.*, s.last_seen AS _seen FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires > ?",
            (self._hash(token), _now()))
        if not row:
            return None
        user = dict(row)
        if user.pop("_seen") < (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds"):
            self.db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (_now(), self._hash(token)))
        return user

    def delete_session(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (self._hash(token),))

    def delete_sessions(self, user_id: int, keep: str | None = None) -> None:
        """Alle Sessions eines Benutzers beenden, außer ``keep`` (Klartext-Token der aktuellen)."""
        self.db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, self._hash(keep) if keep else ""))

    def purge_expired(self) -> None:
        self.db.execute("DELETE FROM sessions WHERE expires <= ?", (_now(),))

    # --- CSRF (an Cookie gebunden, ohne eigenen Zustand)
    def csrf_token(self, base: str | None) -> str:
        return hmac.new(self._secret, (base or "").encode(), hashlib.sha256).hexdigest() if base else ""

    def csrf_valid(self, base: str | None, token: str | None) -> bool:
        return bool(base and token) and secrets.compare_digest(self.csrf_token(base), token or "")


class LoginThrottle:
    """Bremse gegen Brute Force: höchstens ``FAIL_LIMIT`` Fehlversuche je (IP, Benutzer) in ``FAIL_WINDOW`` s (nur im Speicher)."""

    def __init__(self, limit: int = FAIL_LIMIT, window: int = FAIL_WINDOW):
        self.limit, self.window = limit, window
        self._fails: dict[tuple[str, str], list[float]] = {}

    def _recent(self, key: tuple[str, str]) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self._fails.get(key, []) if t > cutoff]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def blocked(self, ip: str | None, username: str) -> bool:
        return len(self._recent((ip or "", username.lower()))) >= self.limit

    def fail(self, ip: str | None, username: str) -> None:
        key = (ip or "", username.lower())
        self._recent(key)
        self._fails.setdefault(key, []).append(time.monotonic())

    def clear(self, ip: str | None, username: str) -> None:
        self._fails.pop((ip or "", username.lower()), None)


# ------------------------------------------------------------ OIDC

class OidcError(Exception):
    """Fehler beim Anmelden über den IdP; ``code`` wählt die Meldung (``login.err_<code>``)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _csv(value: str) -> list[str]:
    return [v.strip().lower() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class OidcConfig:
    """Wirksame OIDC-Einstellungen (Umgebung hat Vorrang vor der Datenbank)."""

    active: bool
    issuer: str
    client_id: str
    client_secret: str
    name: str
    scopes: str
    allowed: str
    admin: str
    from_env: bool

    @property
    def enabled(self) -> bool:
        return bool(self.active and self.issuer and self.client_id)


def valid_issuer(url: str) -> str | None:
    """Bereinigte Issuer-URL oder ``None``: ``https://…``, ``http://`` nur für localhost und private/Loopback-IPs."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018  (löst bei ungültigem Port ValueError aus)
    except ValueError:
        return None
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        return None
    if parts.scheme == "https":
        return url.strip().rstrip("/")
    if parts.scheme == "http":
        if host == "localhost":
            return url.strip().rstrip("/")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return None
        return url.strip().rstrip("/") if ip.is_private or ip.is_loopback else None
    return None


class Oidc:
    """Authorization-Code-Flow mit PKCE (S256), ``state`` und ``nonce``; ID-Token per JWKS geprüft."""

    PENDING_TTL = 600
    DISCOVERY_TTL = 3600
    DEFAULT_SCOPES = "openid email profile"

    def __init__(self, settings: Settings, db: Database | None = None, transport: httpx2.AsyncBaseTransport | None = None):
        self.s = settings
        self.db = db
        self.transport = transport  # nur für Tests (gemockter IdP)
        self._pending: dict[str, dict] = {}
        self._disc: tuple[float, dict] | None = None
        self._jwks: tuple[float, dict] | None = None

    @property
    def env_locked(self) -> bool:
        """Sobald Issuer oder Client-ID per Umgebung gesetzt sind, gilt ausschließlich die Umgebung."""
        return bool(self.s.oidc_issuer or self.s.oidc_client_id)

    def config(self) -> OidcConfig:
        if self.env_locked or not self.db:
            s = self.s
            return OidcConfig(True, (s.oidc_issuer or "").rstrip("/"), s.oidc_client_id or "", s.oidc_client_secret or "", s.oidc_name or "SSO",
                              s.oidc_scopes or self.DEFAULT_SCOPES, s.oidc_allowed or "", s.oidc_admin or "", True)
        g = self.db.get_setting
        return OidcConfig(bool(g("oidc_enabled", False)), str(g("oidc_issuer", "")).rstrip("/"), str(g("oidc_client_id", "")), str(g("oidc_secret", "")),
                          str(g("oidc_name", "")) or "SSO", self.s.oidc_scopes or self.DEFAULT_SCOPES, str(g("oidc_allowed", "")), str(g("oidc_admin", "")), False)

    def reset_cache(self) -> None:
        self._disc = self._jwks = None
        self._pending.clear()

    @property
    def enabled(self) -> bool:
        return self.config().enabled

    @property
    def issuer(self) -> str:
        return self.config().issuer

    def redirect_uri(self, base_url: str, host: str) -> str:
        base = base_url.rstrip("/")
        if self.s.public_url:
            urls = [u.strip().rstrip("/") for u in self.s.public_url.split(",") if u.strip()]
            base = next((u for u in urls if urlsplit(u).netloc == host), urls[0])
        return base + "/auth/oidc/callback"

    def _client(self, **kw) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(timeout=10, transport=self.transport, **kw)

    async def discovery(self) -> dict:
        if self._disc and self._disc[0] > time.monotonic():
            return self._disc[1]
        try:
            async with self._client() as http:
                res = await http.get(f"{self.issuer}/.well-known/openid-configuration")
                res.raise_for_status()
                doc = res.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise OidcError("unreachable") from exc
        if not all(doc.get(k) for k in ("authorization_endpoint", "token_endpoint", "jwks_uri")):
            raise OidcError("failed")
        self._disc = (time.monotonic() + self.DISCOVERY_TTL, doc)
        return doc

    async def _keys(self, doc: dict, refresh: bool = False) -> KeySet:
        if refresh or not self._jwks or self._jwks[0] < time.monotonic():
            try:
                async with self._client() as http:
                    res = await http.get(doc["jwks_uri"])
                    res.raise_for_status()
                    self._jwks = (time.monotonic() + self.DISCOVERY_TTL, res.json())
            except (httpx2.HTTPError, ValueError) as exc:
                raise OidcError("unreachable") from exc
        return KeySet.import_key_set(self._jwks[1])

    def _oauth(self, redirect_uri: str, doc: dict | None = None) -> AsyncOAuth2Client:
        methods = (doc or {}).get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        cfg = self.config()
        if not cfg.client_secret:
            method = "none"
        else:
            method = "client_secret_basic" if "client_secret_basic" in methods else "client_secret_post"
        return AsyncOAuth2Client(
            client_id=cfg.client_id, client_secret=cfg.client_secret or None, scope=cfg.scopes,
            redirect_uri=redirect_uri, token_endpoint_auth_method=method, code_challenge_method="S256",
            transport=self.transport, timeout=10)

    async def start(self, redirect_uri: str, next_url: str) -> tuple[str, str]:
        """Adresse für die Weiterleitung zum IdP und ``state`` (kommt zusätzlich ins Cookie)."""
        doc = await self.discovery()
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if v["expires"] > now}
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        self._pending[state] = {"nonce": nonce, "verifier": verifier, "next": next_url, "redirect_uri": redirect_uri, "expires": now + self.PENDING_TTL}
        async with self._oauth(redirect_uri, doc) as client:
            url, _ = client.create_authorization_url(doc["authorization_endpoint"], state=state, nonce=nonce, code_verifier=verifier)
        return url, state

    async def finish(self, code: str, state: str) -> tuple[dict, str]:
        """Code gegen Token tauschen, ID-Token prüfen: (Claims, ``next``)."""
        pending = self._pending.pop(state, None)
        if not pending or pending["expires"] < time.monotonic():
            raise OidcError("state")
        doc = await self.discovery()
        try:
            async with self._oauth(pending["redirect_uri"], doc) as client:
                token = await client.fetch_token(doc["token_endpoint"], code=code, code_verifier=pending["verifier"])
        except Exception as exc:  # authlib/httpx2: IdP lehnt ab oder ist nicht erreichbar
            raise OidcError("failed") from exc
        id_token = token.get("id_token")
        if not id_token:
            raise OidcError("failed")
        claims = await self._verify(id_token, doc, pending["nonce"])
        return claims, pending["next"]

    async def _verify(self, id_token: str, doc: dict, nonce: str) -> dict:
        registry = jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": self.issuer}, aud={"essential": True, "value": self.config().client_id},
            exp={"essential": True}, sub={"essential": True}, nonce={"essential": True, "value": nonce})
        for refresh in (False, True):  # unbekannte Schlüssel-ID: JWKS einmal neu laden (Schlüsselwechsel)
            try:
                decoded = jwt.decode(id_token, await self._keys(doc, refresh), algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"])
                registry.validate(decoded.claims)
                return decoded.claims
            except OidcError:
                raise
            except JoseError as exc:
                if refresh:
                    raise OidcError("failed") from exc
        raise OidcError("failed")  # pragma: no cover

    # --- Zulassung
    def _identities(self, claims: dict) -> set[str]:
        ids = {str(g).lower() for g in (claims.get("groups") or []) if isinstance(g, str)}
        # Nur bestätigte E-Mails zählen (fehlt email_verified, gilt sie als unbestätigt).
        if claims.get("email") and claims.get("email_verified") is True:
            ids.add(str(claims["email"]).lower())
        return ids

    def allowed(self, claims: dict) -> bool:
        wanted = _csv(self.config().allowed)
        return not wanted or bool(self._identities(claims) & set(wanted))

    def is_admin(self, claims: dict) -> bool | None:
        """``None`` = nicht konfiguriert (OIDC-Benutzer sind dann beim Anlegen Admin)."""
        wanted = _csv(self.config().admin)
        return bool(self._identities(claims) & set(wanted)) if wanted else None

    def find_or_create(self, store: AuthStore, claims: dict) -> dict:
        """Benutzer über (issuer, sub); Namenskonflikt mit anderem Benutzer: Suffix, nie automatisch verknüpfen."""
        sub = str(claims["sub"])
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        admin = self.is_admin(claims)
        user = store.by_oidc(self.issuer, sub)
        if user:
            store.db.execute("UPDATE users SET email = ?, is_admin = ? WHERE id = ?", (email, int(user["is_admin"] if admin is None else admin), user["id"]))
            return store.get_user(user["id"])  # type: ignore[return-value]
        base = str(claims.get("preferred_username") or email or sub)[:60]
        if not USERNAME_RE.match(base):
            base = re.sub(r"[^\w.@+-]", "_", base) or "sso"
        name, n = base, 1
        while store.by_name(name):
            n += 1
            name = f"{base}-{n}"
        return store.create_user(name, None, True if admin is None else admin, email=email, oidc_issuer=self.issuer, oidc_sub=sub)
