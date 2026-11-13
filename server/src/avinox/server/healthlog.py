"""HealthLog-Anbindung: Puls (Apple Watch) für Fahrten ohne Brustgurt-Puls.

Der Server meldet sich einmal mit Benutzer und Passwort an und merkt sich nur die
Tokens (Zugriffs- und Erneuerungs-Token) in der ``settings``-Tabelle. Das Passwort
und ein Code für den zweiten Faktor werden weder gespeichert noch geloggt.
HTTP läuft über ``urllib`` (wie ``geocode.py``); ``_request`` ist die einzige Stelle
mit Netzzugriff und lässt sich in Tests ersetzen.

Fehler werden als ``HealthLogError`` mit einem stabilen ``code`` gemeldet:
``invalid_url``, ``unreachable``, ``bad_response``, ``invalid_credentials``,
``mfa_invalid``, ``not_connected``, ``disconnected`` (Tokens abgelaufen/widerrufen).
"""

from __future__ import annotations

import bisect
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

log = logging.getLogger("avinox.healthlog")

TIMEOUT_S = 20
SEARCH_WINDOW = timedelta(minutes=10)
"""Workouts, die höchstens so weit vor/nach der Fahrt liegen, kommen in Frage."""
MAX_GAP_S = 180
"""Größere Lücken zwischen zwei Workout-Punkten bleiben ohne Wert (``hrSeries`` bündelt je nach Dauer in größere Abschnitte)."""
MAX_GAP_MEASURED_S = 900
"""Grenze, sobald einer der Nachbarn eine Einzelmessung ist."""
TOKEN_MARGIN_S = 60

KEYS = ("url", "enabled", "username", "token", "token_expires", "refresh_token")


class HealthLogError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class HrResult(NamedTuple):
    points: list[tuple[int, int, str]]
    """(Unix-Sekunden, bpm, Art ``w``/``m``), aufsteigend."""
    source: str
    """``healthlog_workout``, ``healthlog_measurements`` oder ``healthlog_mixed``."""
    workout_id: str | None


# ------------------------------------------------------------------ Einstellungen


def _get(db, key: str, default=None):
    return db.get_setting(f"healthlog_{key}", default)


def _set(db, key: str, value) -> None:
    db.set_setting(f"healthlog_{key}", value)


def normalize_url(url: str) -> str:
    """Basis-URL ohne Pfad-Ende ``/`` und ``/api``; ``HealthLogError('invalid_url')`` bei Unbrauchbarem."""
    u = (url or "").strip().rstrip("/")
    if u.lower().endswith("/api"):
        u = u[:-4].rstrip("/")
    parts = urllib.parse.urlsplit(u)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HealthLogError("invalid_url", "URL muss mit http:// oder https:// beginnen")
    return u


def is_connected(db) -> bool:
    return bool(_get(db, "url") and _get(db, "refresh_token"))


def is_active(db) -> bool:
    return bool(_get(db, "enabled", False)) and is_connected(db)


def status(db) -> dict:
    """Öffentlicher Stand ohne Tokens."""
    return {
        "url": _get(db, "url") or "",
        "enabled": bool(_get(db, "enabled", False)),
        "connected": is_connected(db),
        "username": _get(db, "username") or "",
    }


def set_url(db, url: str) -> None:
    """Speichert die URL; bei anderer URL werden die Tokens verworfen (gehören zum alten Server)."""
    new = normalize_url(url) if url and url.strip() else ""
    if new != (_get(db, "url") or ""):
        _clear_tokens(db)
    _set(db, "url", new)


def set_enabled(db, enabled: bool) -> None:
    _set(db, "enabled", bool(enabled))


def _clear_tokens(db) -> None:
    for key in ("token", "token_expires", "refresh_token", "username"):
        _set(db, key, None)


def _store_bundle(db, bundle: dict, username: str | None = None) -> None:
    """Neues Token-Paar sofort speichern (Erneuerungs-Token gelten nur einmal)."""
    _set(db, "token", bundle.get("token"))
    _set(db, "token_expires", bundle.get("tokenExpiresAt"))
    _set(db, "refresh_token", bundle.get("refreshToken"))
    user = bundle.get("user") if isinstance(bundle.get("user"), dict) else {}
    name = user.get("username") or username
    if name:
        _set(db, "username", name)


# ------------------------------------------------------------------ HTTP


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keine Weiterleitungen folgen: sonst ginge der Bearer-Token an einen fremden Host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(method: str, url: str, headers: dict, body: dict | None, timeout: float = TIMEOUT_S) -> tuple[int, dict]:
    """(HTTP-Status, JSON-Hülle). Netzfehler → ``HealthLogError('unreachable')``."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Accept": "application/json", "User-Agent": "avinox-ridedata-server", **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            status_code, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status_code, raw = exc.code, exc.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HealthLogError("unreachable", f"HealthLog nicht erreichbar: {getattr(exc, 'reason', exc)}") from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        payload = {}
    return status_code, payload if isinstance(payload, dict) else {}


def _api(base: str, path: str, params: dict | None = None) -> str:
    url = f"{base}/api{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def _error_code(payload: dict) -> str:
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return str(meta.get("errorCode") or "")


# ------------------------------------------------------------------ Anmeldung


def _login(base: str, username: str, password: str) -> tuple[dict | None, dict]:
    """(Bundle oder None bei MFA, meta). ``invalid_credentials`` bei 401/403/422."""
    code, payload = _request("POST", _api(base, "/auth/login"), {"X-Client-Type": "native"}, {"email": username, "password": password})
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if code in (401, 403, 422):
        raise HealthLogError("invalid_credentials", "Anmeldung abgelehnt")
    if code != 200:
        raise HealthLogError("bad_response", f"Unerwartete Antwort von HealthLog (HTTP {code})")
    if meta.get("mfaRequired"):
        return None, meta
    bundle = payload.get("data")
    if not isinstance(bundle, dict) or not bundle.get("token") or not bundle.get("refreshToken"):
        raise HealthLogError("bad_response", "Antwort ohne Token")
    return bundle, meta


def connect(db, url: str | None, username: str, password: str, mfa_code: str | None = None) -> dict:
    """Meldet an und speichert nur die Tokens. Rückgabe: ``{"mfa_required": bool, "methods": [...]}``.

    Verlangt HealthLog einen zweiten Faktor und ``mfa_code`` fehlt, wird nichts gespeichert und
    ``mfa_required`` gemeldet; der Aufrufer sendet dieselben Angaben erneut mit Code.
    """
    if url is not None and url.strip():
        set_url(db, url)
    base = _get(db, "url")
    if not base:
        raise HealthLogError("invalid_url", "Keine HealthLog-URL eingestellt")
    username = (username or "").strip()
    if not username or not password:
        raise HealthLogError("invalid_credentials", "Benutzer und Passwort angeben")
    bundle, meta = _login(base, username, password)
    if bundle is None:
        methods = [m for m in (meta.get("methods") or []) if isinstance(m, str)]
        ticket = meta.get("mfaTicket")
        if not mfa_code or not str(mfa_code).strip():
            return {"mfa_required": True, "methods": methods}
        if not ticket:
            raise HealthLogError("bad_response", "Antwort ohne MFA-Ticket")
        method = "totp" if "totp" in methods or not methods else "recovery"
        code, payload = _request(
            "POST", _api(base, "/auth/mfa/verify"), {"X-Client-Type": "native"},
            {"mfaTicket": ticket, "method": method, "code": str(mfa_code).strip()},
        )
        if code in (400, 401, 403, 422):
            raise HealthLogError("mfa_invalid", "Code nicht akzeptiert")
        if code != 200:
            raise HealthLogError("bad_response", f"Unerwartete Antwort von HealthLog (HTTP {code})")
        bundle = payload.get("data")
        if not isinstance(bundle, dict) or not bundle.get("token") or not bundle.get("refreshToken"):
            raise HealthLogError("bad_response", "Antwort ohne Token")
    _store_bundle(db, bundle, username)
    if _get(db, "enabled") is None:
        set_enabled(db, True)
    return {"mfa_required": False, "methods": []}


def disconnect(db) -> None:
    """Widerruft den Erneuerungs-Token (best effort) und vergisst alle Tokens."""
    base, refresh = _get(db, "url"), _get(db, "refresh_token")
    if base and refresh:
        try:
            _request("POST", _api(base, "/auth/refresh"), {"X-Client-Type": "native"}, {"refreshToken": refresh, "revoke": True})
        except HealthLogError as exc:
            log.info("HealthLog-Token konnte nicht widerrufen werden: %s", exc)
    _clear_tokens(db)


_refresh_lock = threading.Lock()


def _refresh(db, stale_token: str | None = None) -> None:
    """Tauscht den Erneuerungs-Token (gilt nur einmal) und speichert das neue Paar sofort."""
    with _refresh_lock:
        if stale_token is not None and _get(db, "token") != stale_token:
            return  # ein anderer Thread hat schon erneuert
        base, refresh = _get(db, "url"), _get(db, "refresh_token")
        if not base or not refresh:
            raise HealthLogError("not_connected", "Nicht mit HealthLog verbunden")
        code, payload = _request("POST", _api(base, "/auth/refresh"), {"X-Client-Type": "native"}, {"refreshToken": refresh})
        if code == 401:
            log.warning("HealthLog-Verbindung getrennt (%s)", _error_code(payload) or "401")
            _clear_tokens(db)
            raise HealthLogError("disconnected", "Verbindung zu HealthLog abgelaufen, bitte neu anmelden")
        bundle = payload.get("data")
        if code != 200 or not isinstance(bundle, dict) or not bundle.get("token") or not bundle.get("refreshToken"):
            raise HealthLogError("bad_response", f"Token-Erneuerung fehlgeschlagen (HTTP {code})")
        _store_bundle(db, bundle)


def _token_fresh(db) -> bool:
    exp = _get(db, "token_expires")
    if not _get(db, "token") or not exp:
        return False
    try:
        when = datetime.fromisoformat(exp)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when - timedelta(seconds=TOKEN_MARGIN_S) > datetime.now(UTC)


def _get_json(db, path: str, params: dict | None = None) -> dict:
    """GET mit Bearer-Token; bei abgelaufenem Token oder 401 einmal erneuern und wiederholen. Gibt ``data`` zurück."""
    if not is_connected(db):
        raise HealthLogError("not_connected", "Nicht mit HealthLog verbunden")
    if not _token_fresh(db):
        _refresh(db, _get(db, "token"))
    for attempt in (1, 2):
        base, token = _get(db, "url"), _get(db, "token")
        code, payload = _request("GET", _api(base, path, params), {"Authorization": f"Bearer {token}", "X-Client-Type": "native"}, None)
        if code == 401 and attempt == 1:
            _refresh(db, token)
            continue
        if code == 401:
            raise HealthLogError("disconnected", "HealthLog lehnt den Zugriff ab")
        if code != 200:
            raise HealthLogError("bad_response", f"Unerwartete Antwort von HealthLog (HTTP {code})")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HealthLogError("bad_response", "Antwort ohne Daten")
        return data
    raise HealthLogError("bad_response")  # pragma: no cover


# ------------------------------------------------------------------ Puls abrufen


def _parse_time(value) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(ts, UTC)
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return when if when.tzinfo else when.replace(tzinfo=UTC)
    return None


def _overlap_s(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    return (min(a1, b1) - max(a0, b0)).total_seconds()


def _curve_points(workout: dict, start: datetime) -> list[tuple[int, int]]:
    """Puls aus ``hrSeries`` (tSec relativ zum Start, Mittelwert je Abschnitt)."""
    curve = workout.get("hrSeries")
    pts: list[tuple[int, int]] = []
    if isinstance(curve, dict) and isinstance(curve.get("points"), list):
        base = int(start.timestamp())
        for p in curve["points"]:
            if isinstance(p, dict) and isinstance(p.get("tSec"), (int, float)) and isinstance(p.get("mean"), (int, float)) and p["mean"] > 0:
                pts.append((base + int(p["tSec"]), round(p["mean"])))
    return sorted(pts)


_T_KEYS = ("t", "time", "timestamp", "ts", "date", "startDate", "startedAt", "measuredAt")
_V_KEYS = ("bpm", "hr", "heartRate", "value", "mean", "avg")


def _raw_sample_points(workout: dict) -> list[tuple[int, int]]:
    """``samples.samples`` ist untypisiert: nur Listen aus Objekten mit erkennbarer Zeit und Wert, sonst leer."""
    block = workout.get("samples")
    items = block.get("samples") if isinstance(block, dict) else None
    if not isinstance(items, list):
        return []
    pts: list[tuple[int, int]] = []
    for it in items:
        if not isinstance(it, dict):
            return []
        t = next((_parse_time(it[k]) for k in _T_KEYS if k in it and _parse_time(it[k])), None)
        v = next((it[k] for k in _V_KEYS if isinstance(it.get(k), (int, float)) and not isinstance(it.get(k), bool)), None)
        if t is None or v is None or not 20 <= v <= 260:
            continue
        pts.append((int(t.timestamp()), round(v)))
    return sorted(pts)


def _workout_points(workout: dict) -> list[tuple[int, int]]:
    start = _parse_time(workout.get("startedAt"))
    if start is None:
        return []
    return _curve_points(workout, start) or _raw_sample_points(workout)


def _pulse_points(db, start: datetime, end: datetime) -> list[tuple[int, int]]:
    data = _get_json(
        db, "/measurements",
        {"type": "PULSE", "from": (start - timedelta(minutes=1)).isoformat(), "to": (end + timedelta(minutes=1)).isoformat(), "limit": 5000},
    )
    pts: list[tuple[int, int]] = []
    for row in data.get("measurements") or []:
        if not isinstance(row, dict) or row.get("valueMin") is not None or row.get("valueMax") is not None:
            continue  # Stundenwerte (Min/Max-Bündel) taugen nicht für eine Kurve
        when, value = _parse_time(row.get("measuredAt")), row.get("value")
        if when is None or not isinstance(value, (int, float)) or isinstance(value, bool) or not 20 <= value <= 260:
            continue
        pts.append((int(when.timestamp()), round(value)))
    return sorted(set(pts))


def combine(workout_series: list[list[tuple[int, int]]], measurements: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """Eine Zeitreihe ``(ts, bpm, art)`` mit art ``w`` (Workout) oder ``m`` (Einzelmessung).

    Im Zeitabschnitt eines Workouts gelten dessen Punkte, außerhalb die Einzelmessungen.
    """
    spans = [(min(p[0] for p in ser), max(p[0] for p in ser)) for ser in workout_series if ser]
    out: dict[int, tuple[int, int, str]] = {}
    for ts, bpm in measurements:
        if not any(a <= ts <= b for a, b in spans):
            out[ts] = (ts, bpm, "m")
    for ser in workout_series:
        for ts, bpm in ser:
            out[ts] = (ts, bpm, "w")
    return [out[k] for k in sorted(out)]


def source_of(points: list) -> str:
    kinds = {p[2] for p in points}
    if kinds == {"w"}:
        return "healthlog_workout"
    if kinds == {"m"}:
        return "healthlog_measurements"
    return "healthlog_mixed"


def fetch_heart_rate(db, start_utc: datetime, end_utc: datetime) -> HrResult:
    """Puls zur Fahrt: alle überlappenden Workouts plus alle PULSE-Messwerte, zusammengeführt. Leere Liste = nichts gefunden."""
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=UTC)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=UTC)
    data = _get_json(
        db, "/workouts",
        {"since": (start_utc - SEARCH_WINDOW).isoformat(), "until": (end_utc + SEARCH_WINDOW).isoformat(), "limit": 50},
    )
    series: list[list[tuple[int, int]]] = []
    ids: list[str] = []
    for w in data.get("workouts") or []:
        if not isinstance(w, dict) or not w.get("id"):
            continue
        w0, w1 = _parse_time(w.get("startedAt")), _parse_time(w.get("endedAt"))
        if w0 is None or w1 is None or _overlap_s(start_utc - SEARCH_WINDOW, end_utc + SEARCH_WINDOW, w0, w1) <= 0:
            continue
        pts = _workout_points(_get_json(db, f"/workouts/{urllib.parse.quote(str(w['id']), safe='')}"))
        if pts:
            series.append(pts)
            ids.append(str(w["id"]))
    points = combine(series, _pulse_points(db, start_utc, end_utc))
    return HrResult(points, source_of(points) if points else "healthlog_measurements", ",".join(ids) or None)


# ------------------------------------------------------------------ Zusammenführen


def _gap_limit(a: tuple, b: tuple, max_gap: int, max_gap_measured: int) -> int:
    return max_gap if a[2] == "w" and b[2] == "w" else max_gap_measured


def interpolate(points: list, ts: float, max_gap: int = MAX_GAP_S, max_gap_measured: int = MAX_GAP_MEASURED_S) -> int | None:
    """Pulswert zur Zeit ``ts`` aus ``(ts, bpm, art)``: lineare Interpolation.

    Lücken über der Grenze bleiben ohne Wert: ``max_gap`` zwischen zwei Workout-Punkten,
    sonst ``max_gap_measured`` (Einzelmessungen kommen nur alle paar Minuten).
    Punkte ohne Art (2-Tupel) gelten als Workout.
    """
    if not points:
        return None
    pts = [p if len(p) > 2 else (p[0], p[1], "w") for p in points]
    times = [p[0] for p in pts]
    i = bisect.bisect_left(times, ts)
    if i < len(pts) and pts[i][0] == ts:
        return pts[i][1]
    if i == 0:
        return pts[0][1] if pts[0][0] - ts <= (max_gap if pts[0][2] == "w" else max_gap_measured) else None
    if i == len(pts):
        return pts[-1][1] if ts - pts[-1][0] <= (max_gap if pts[-1][2] == "w" else max_gap_measured) else None
    a, b = pts[i - 1], pts[i]
    if b[0] - a[0] > _gap_limit(a, b, max_gap, max_gap_measured):
        return None
    return round(a[1] + (b[1] - a[1]) * (ts - a[0]) / (b[0] - a[0]))


def has_belt_hr(ride) -> bool:
    return any(s.heart_rate_bpm for s in ride.samples)


def merge_heart_rate(ride, points: list) -> bool:
    """Setzt ``heart_rate_bpm`` der Samples aus ``points``, nur wenn die Fahrt keinen Gurt-Puls hat.

    Braucht Uhrzeit-Zeitstempel (Header mit Uhrzeit). Gibt zurück, ob mindestens ein Wert gesetzt wurde.
    """
    if has_belt_hr(ride) or not points or not ride.header.has_wallclock:
        return False
    points = sorted(p if len(p) > 2 else (p[0], p[1], "w") for p in points)  # einmal statt je Sample normalisieren
    any_set = False
    for s in ride.samples:
        if s.timestamp is None or s.timestamp < ride.header.start - 3600:
            continue
        bpm = interpolate(points, s.timestamp)
        if bpm:
            s.heart_rate_bpm = bpm
            any_set = True
    return any_set
