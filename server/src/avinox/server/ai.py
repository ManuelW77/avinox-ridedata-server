"""KI-Auswertung: Python rechnet Fakten, ein LLM formuliert daraus kurze Texte.

Anbieter über das OpenAI-kompatible Format (``POST {base_url}/chat/completions``, Bearer-Key).
An das LLM gehen **nur Kennzahlen**: keine GPS-Daten, Ortsnamen, Fahrtnamen, Dateinamen,
Seriennummern oder Benutzernamen (``clean`` entfernt solche Schlüssel rekursiv).

``_post`` ist die einzige Stelle mit Netzzugriff und lässt sich in Tests ersetzen.
Fehler werden als ``AiError`` mit stabilem ``code`` gemeldet: ``not_configured``, ``invalid_key``,
``bad_url``, ``bad_request``, ``rate_limited``, ``unreachable``, ``bad_response``, ``http_<status>``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import deque
from datetime import date, datetime
from urllib.parse import urlsplit

import httpx2

from . import dashboard

log = logging.getLogger("avinox.ai")

PROMPT_VERSION = 1
TIMEOUT_S = 90
TEMPERATURE = 0.4
MIN_KM = 1.0
"""Kürzere Fahrten bekommen keinen Text (Status ``none``)."""

PRESETS = [
    {"id": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"id": "anthropic", "name": "Anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-haiku-5-5"},
    {"id": "mistral", "name": "Mistral", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest"},
    {"id": "groq", "name": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    {"id": "ollama", "name": "Ollama (lokal)", "base_url": "http://localhost:11434/v1", "model": "llama3.1"},
    {"id": "custom", "name": "Eigene Adresse", "base_url": "", "model": ""},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}
NO_KEY_PROVIDERS = ("ollama", "custom")
"""Anbieter, bei denen kein Schlüssel nötig ist (lokal bzw. unbekannt)."""
LANGS = ("de", "en")

RIDE_KEYS = ("summary", "drive", "power", "hill", "compare")
PERIOD_KEYS = ("summary", "drive", "power", "hill", "bests", "trend")
FATAL_CODES = ("not_configured", "invalid_key", "bad_url")
"""Bei diesen Fehlern lohnt kein Weiterarbeiten an der Warteschlange."""


class AiError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ------------------------------------------------------------------ Einstellungen


def _get(db, key: str, default=None):
    return db.get_setting(f"ai_{key}", default)


def _set(db, key: str, value) -> None:
    db.set_setting(f"ai_{key}", value)


def lang_of(db) -> str:
    lang = _get(db, "lang", "de")
    return lang if lang in LANGS else "de"


def has_key(db) -> bool:
    return bool(_get(db, "api_key"))


def is_configured(db) -> bool:
    if not (base_url_of(db) and model_of(db)):
        return False
    return has_key(db) or _get(db, "provider") in NO_KEY_PROVIDERS


def is_active(db) -> bool:
    return bool(_get(db, "enabled", False)) and is_configured(db)


def _preset(db) -> dict:
    return PRESET_BY_ID.get(_get(db, "provider") or "openrouter") or PRESET_BY_ID["custom"]


def model_of(db) -> str:
    """Gesetztes Modell, sonst das Beispielmodell des Anbieters."""
    return _get(db, "model") or _preset(db)["model"]


def base_url_of(db) -> str:
    return (_get(db, "base_url") or _preset(db)["base_url"]).rstrip("/")


def settings_view(db) -> dict:
    """Öffentlicher Stand; der Schlüssel selbst nie, nur ein Hinweis auf die letzten vier Zeichen."""
    key = _get(db, "api_key") or ""
    return {
        "enabled": bool(_get(db, "enabled", False)),
        "provider": _get(db, "provider") or "openrouter",
        "base_url": base_url_of(db),
        "model": model_of(db),
        "lang": lang_of(db),
        "has_key": bool(key),
        "key_hint": "…" + key[-4:] if key else None,
        "presets": PRESETS,
        "last_error": _get(db, "last_error") or None,
    }


def _origin(url: str) -> tuple:
    """Schema, Host (klein) und Port: der Schlüssel gilt nur für genau diesen Empfänger."""
    u = urlsplit(url or "")
    try:
        port = u.port
    except ValueError:
        port = None
    return (u.scheme.lower(), (u.hostname or "").lower(), port or {"http": 80, "https": 443}.get(u.scheme.lower()))


def update_settings(db, payload: dict) -> None:
    """Teilmenge setzen. Typfehler → ``AiError('invalid', ...)``. ``api_key`` fehlt = unverändert, ``""`` = löschen."""
    if not isinstance(payload, dict):
        raise AiError("invalid", "Body muss ein Objekt sein")
    for key in ("provider", "base_url", "model", "lang", "api_key"):
        if key in payload and not isinstance(payload[key], str):
            raise AiError("invalid", f"{key} muss ein Text sein")
    if "enabled" in payload and not isinstance(payload["enabled"], bool):
        raise AiError("invalid", "enabled muss true oder false sein")
    if "provider" in payload and payload["provider"] not in PRESET_BY_ID:
        raise AiError("invalid", "Unbekannter Anbieter")
    if "lang" in payload and payload["lang"] not in LANGS:
        raise AiError("invalid", "lang muss de oder en sein")
    old_origin = _origin(base_url_of(db))  # vor allen Änderungen: wohin der Schlüssel bisher ging
    base_url = None
    if "base_url" in payload:
        base_url = payload["base_url"].strip().rstrip("/")
        if base_url and not re.match(r"^https?://[^\s/]+", base_url):
            raise AiError("invalid", "base_url muss mit http:// oder https:// beginnen")
        if base_url and (urlsplit(base_url).query or urlsplit(base_url).fragment or "@" in urlsplit(base_url).netloc):
            raise AiError("invalid", "base_url darf keine Zugangsdaten, ? oder # enthalten")
    if "provider" in payload and payload["provider"] != (_get(db, "provider") or "openrouter"):
        preset = PRESET_BY_ID[payload["provider"]]
        if "base_url" not in payload and preset["base_url"]:
            base_url = preset["base_url"]
        if "model" not in payload and preset["model"]:
            _set(db, "model", preset["model"])
    if "provider" in payload:
        _set(db, "provider", payload["provider"])
    if base_url is not None:
        # Schlüssel gehört zum Anbieter: neuer Host ohne neuen Schlüssel → alten löschen (sonst ginge er an die neue Adresse)
        if "api_key" not in payload and _origin(base_url) != old_origin:
            _set(db, "api_key", None)
        _set(db, "base_url", base_url)
    if "model" in payload:
        _set(db, "model", payload["model"].strip())
    if "lang" in payload:
        _set(db, "lang", payload["lang"])
    if "enabled" in payload:
        _set(db, "enabled", payload["enabled"])
    if "api_key" in payload:
        _set(db, "api_key", payload["api_key"].strip() or None)


# ------------------------------------------------------------------ LLM-Aufruf


def _post(url: str, headers: dict, body: dict, timeout: float = TIMEOUT_S) -> tuple[int, dict]:
    """(HTTP-Status, JSON-Hülle). Netzfehler → ``AiError('unreachable')``. Einzige Netzstelle."""
    try:
        r = httpx2.post(url, headers=headers, json=body, timeout=timeout, follow_redirects=False)
    except httpx2.InvalidURL as exc:
        raise AiError("bad_url", "Adresse ungültig") from exc
    except httpx2.HTTPError as exc:
        raise AiError("unreachable", f"Nicht erreichbar: {type(exc).__name__}") from exc
    try:
        data = r.json()
    except ValueError:
        data = {}
    return r.status_code, data if isinstance(data, dict) else {}


def _error_text(data: dict) -> str:
    err = data.get("error")
    msg = err.get("message") if isinstance(err, dict) else err
    return str(msg)[:200] if msg else ""


def chat(db, messages: list[dict], json_mode: bool = True) -> str:
    """Eine Chat-Anfrage; Text der Antwort. ``response_format`` wird bei HTTP 400 einmal weggelassen."""
    if not is_configured(db):
        raise AiError("not_configured", "KI ist nicht eingerichtet")
    base = base_url_of(db)
    key = _get(db, "api_key") or ""
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "avinox-ridedata-server"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if _get(db, "provider") == "openrouter" or "openrouter.ai" in base:
        headers["X-Title"] = "Avinox Ride Data"
    body = {"model": model_of(db), "messages": messages, "temperature": TEMPERATURE}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    status, data = _post(f"{base}/chat/completions", headers, body, TIMEOUT_S)
    if status == 400 and json_mode:
        body.pop("response_format")
        status, data = _post(f"{base}/chat/completions", headers, body, TIMEOUT_S)
    if status in (401, 403):
        raise AiError("invalid_key", _error_text(data) or "Schlüssel abgelehnt")
    if status == 404:
        raise AiError("bad_url", _error_text(data) or "Adresse oder Modell nicht gefunden")
    if status == 400:
        raise AiError("bad_request", _error_text(data) or "Anfrage abgelehnt")
    if status == 429:
        raise AiError("rate_limited", _error_text(data) or "Zu viele Anfragen")
    if status >= 400:
        raise AiError(f"http_{status}", _error_text(data) or f"HTTP {status}")
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AiError("bad_response", "Antwort ohne Text") from exc
    if not isinstance(text, str) or not text.strip():
        raise AiError("bad_response", "Leere Antwort")
    return text


# ------------------------------------------------------------------ Antwort lesen


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def parse_json(text: str) -> dict:
    """Toleranter Parser: Code-Fences entfernen, erstes ``{...}`` lesen. Sonst ``AiError('bad_response')``."""
    s = (text or "").strip()
    m = _FENCE.search(s)
    if m:
        s = m.group(1).strip()
    start = s.find("{")
    if start < 0:
        raise AiError("bad_response", "Keine JSON-Antwort")
    try:
        obj, _ = json.JSONDecoder().raw_decode(s[start:])
    except ValueError as exc:
        raise AiError("bad_response", "JSON nicht lesbar") from exc
    if not isinstance(obj, dict):
        raise AiError("bad_response", "JSON ist kein Objekt")
    return obj


def _plain(value) -> str:
    """Reiner Text: Markdown-Zeichen und Aufzählungszeichen weg, Leerraum glätten."""
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    if not isinstance(value, str):
        return ""
    s = re.sub(r"[*_`#>]+", "", value)
    s = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize(obj: dict, kind: str) -> dict:
    """Auf die Content-Schlüssel des Vertrags bringen: Texte (reiner Text) und ``tips`` (höchstens 4)."""
    keys = RIDE_KEYS if kind == "ride" else PERIOD_KEYS
    out = {k: _plain(obj.get(k)) for k in keys}
    tips = obj.get("tips")
    if isinstance(tips, str):
        tips = [tips]
    out["tips"] = [t for t in (_plain(x) for x in (tips if isinstance(tips, list) else [])) if t][:4]
    return out


# ------------------------------------------------------------------ Prompt

_SYSTEM = {
    "de": (
        "Du bist ein erfahrener E-MTB-Coach und wertest Fahrdaten eines Bosch-/DJI-Avinox-artigen Mittelmotors aus. "
        "Schließe ausschließlich aus den gelieferten Fakten (JSON); erfinde nichts, rate keine Orte, Wetter oder Absichten. "
        "Zitiere Zahlen sparsam und korrekt (gerundet, mit Einheit). Schreibe freundlich und sprich den Fahrer mit Du an. "
        "Reiner Text: kein Markdown, keine Aufzählungszeichen, kein HTML. Jedes Textfeld hat 2 bis 4 Sätze; "
        "fehlen die Daten für ein Feld, gib einen leeren String zurück. "
        "'tips' enthält 0 bis 4 konkrete, umsetzbare Hinweise (je ein Satz), nur wenn die Daten sie stützen. Beispiele: "
        "viel Zeit in schweren Gängen bei niedriger Trittfrequenz -> früher in leichtere Gänge schalten und 70 bis 90 U/min anpeilen; "
        "kaum Schaltvorgänge -> die Schaltung öfter nutzen; hoher Motoranteil im flachen Gelände -> eine kleinere Unterstützungsstufe "
        "spart Akku; Puls lange sehr hoch -> Intensität dosieren. Ein höherer Gang bedeutet einen schwereren Gang. "
        "Antworte ausschließlich mit einem JSON-Objekt mit genau diesen Schlüsseln: {keys}."
    ),
    "en": (
        "You are an experienced e-MTB coach analysing ride data from a mid-drive motor (DJI Avinox style). "
        "Draw conclusions only from the facts provided (JSON); invent nothing, do not guess places, weather or intentions. "
        "Quote numbers sparingly and correctly (rounded, with unit). Be friendly and address the rider directly. "
        "Plain text only: no Markdown, no bullet characters, no HTML. Every text field has 2 to 4 sentences; "
        "if the data does not support a field, return an empty string. "
        "'tips' holds 0 to 4 concrete, actionable hints (one sentence each), only if the data supports them. Examples: "
        "lots of time in hard gears at low cadence -> shift to easier gears earlier and aim for 70 to 90 rpm; "
        "hardly any gear changes -> use the gearbox more; high motor share on flat terrain -> a lower assist level saves battery; "
        "heart rate very high for long -> pace the effort. A higher gear number means a harder gear. "
        "Reply with a JSON object only, with exactly these keys: {keys}."
    ),
}

_USER = {
    "de": {"ride": "Fakten zu einer Fahrt:", "period": "Fakten zu einem Zeitraum (mehrere Fahrten):"},
    "en": {"ride": "Facts about one ride:", "period": "Facts about a period (several rides):"},
}


def build_messages(kind: str, facts: dict, lang: str) -> list[dict]:
    lang = lang if lang in LANGS else "de"
    keys = ", ".join(f'"{k}"' for k in (RIDE_KEYS if kind == "ride" else PERIOD_KEYS) + ("tips",))
    system = _SYSTEM[lang].replace("{keys}", keys)
    user = f"{_USER[lang][kind]}\n{json.dumps(facts, ensure_ascii=False, sort_keys=True)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def input_hash(facts: dict, model: str) -> str:
    raw = json.dumps(facts, sort_keys=True, ensure_ascii=False) + "\n" + model + "\n" + str(PROMPT_VERSION)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ Fakten

BLOCKED_KEYS = frozenset({
    "lat", "lon", "lng", "latitude", "longitude", "track", "geojson", "bbox", "preview", "points", "coords", "coordinates", "gps",
    "places", "place", "location", "name", "custom_name", "title_name", "path", "file", "filename", "files",
    "serial", "serial_number", "user", "username", "owner", "email", "thumb", "href", "url", "ride", "id", "ride_id", "device_id",
})
"""Schlüssel, die nie an das LLM gehen (Koordinaten, Orte, Namen, Dateien, Kennungen)."""


def clean(obj):
    """Rekursiv: gesperrte Schlüssel und ``None`` entfernen."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if str(k).lower() not in BLOCKED_KEYS and v is not None}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, float):  # kurze Zahlen: weniger Token, LLM zitiert keine Scheingenauigkeit
        return round(obj, 2 if abs(obj) < 10 else 1)
    return obj


def _r(v, d: int = 0):
    if v is None:
        return None
    v = round(float(v), d)
    return int(v) if d == 0 else v


def _min(sec) -> float | None:
    return None if sec is None else round(sec / 60, 1)


def _pct_of(counts: dict, total: float) -> dict:
    return {k: _r(100 * v / total, 1) for k, v in counts.items() if total and v}


def _gear_int(key: str) -> int | None:
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


def gear_stats(gears: dict, km: float, max_gear: int | None) -> dict | None:
    """Zeit je Gang, Schaltvorgänge, Ø-Trittfrequenz je Gang, Anteil in den 3 schwersten/leichtesten Gängen."""
    secs = {k: v for k, v in ((gears or {}).get("seconds_per_gear") or {}).items() if v}
    if not secs:
        return None
    total = sum(secs.values())
    ints = {k: _gear_int(k) for k in secs}
    top = max_gear or max((i for i in ints.values() if i is not None), default=None)
    out = {
        "minutes_per_gear": {k: _min(v) for k, v in sorted(secs.items(), key=lambda kv: (ints[kv[0]] is None, ints[kv[0]] or 0))},
        "shifts_total": (gears or {}).get("changes"),
        "shifts_per_km": _r(((gears or {}).get("changes") or 0) / km, 1) if km else None,
        "avg_cadence_rpm_per_gear": {k: _r(m.get("avg_cadence_rpm")) for k, m in ((gears or {}).get("per_gear") or {}).items()
                                     if m.get("avg_cadence_rpm") is not None},
    }
    if top is not None:
        out["heaviest_3_gears_time_pct"] = _r(100 * sum(v for k, v in secs.items() if ints[k] is not None and ints[k] > top - 3) / total, 1)
        out["lightest_3_gears_time_pct"] = _r(100 * sum(v for k, v in secs.items() if ints[k] is not None and ints[k] <= 3) / total, 1)
        out["highest_gear_number"] = top
    return out


def low_cadence_pct(series: dict | None) -> float | None:
    """Anteil der Zeit mit Trittfrequenz unter 50 U/min beim Treten (aus der geglätteten Zeitreihe)."""
    if not series or not series.get("rows"):
        return None
    f = series["fields"]
    try:
        ci, pi, si = f.index("cadence_rpm"), f.index("rider_power_w"), f.index("speed_kmh")
    except ValueError:
        return None
    ped = low = 0
    for row in series["rows"]:
        if row[pi] and row[pi] > 0 and row[ci] is not None and row[si] and row[si] >= 1:
            ped += 1
            low += row[ci] < 50
    return _r(100 * low / ped, 1) if ped else None


def _ride_metrics(summary: dict, gears: dict, energy: dict, battery: dict, ascent: float | None) -> dict:
    """Flache, zwischen Fahrten vergleichbare Kennzahlen."""
    km = summary.get("distance_km") or 0
    return {
        "distance_km": _r(km, 1),
        "duration_min": _r((summary.get("duration_s") or 0) / 60),
        "ascent_m": _r(ascent if ascent is not None else summary.get("ascent_m")),
        "avg_speed_kmh": _r(summary.get("avg_speed_kmh"), 1),
        "max_speed_kmh": _r(summary.get("max_speed_kmh"), 1),
        "rider_power_avg_w": _r(summary.get("avg_rider_power_w")),
        "motor_power_avg_w": _r(summary.get("avg_motor_power_w")),
        "motor_share_pct": _r((energy or {}).get("motor_share_pct")),
        "cadence_avg_rpm": _r(summary.get("avg_cadence_rpm")),
        "heart_rate_avg_bpm": _r(summary.get("avg_heart_rate_bpm")),
        "shifts_per_km": _r(((gears or {}).get("changes") or 0) / km, 1) if km and gears else None,
        "km_per_battery_pct": _r((battery or {}).get("km_per_pct"), 2),
    }


RANK_KEYS = ("distance_km", "ascent_m", "avg_speed_kmh", "max_speed_kmh", "rider_power_avg_w", "motor_share_pct")


def _others(db, exclude_pk: int) -> tuple[list[dict], int | None]:
    """Kennzahlen aller anderen Fahrten ≥ 1 km und die größte Gangnummer über alle Fahrten."""
    rows = db.query(
        "SELECT id, ascent_m, json_extract(analysis_json,'$.summary') AS s, json_extract(analysis_json,'$.gears') AS g, "
        "json_extract(analysis_json,'$.energy') AS e, json_extract(analysis_json,'$.battery') AS b FROM rides WHERE distance_m >= ?",
        (int(MIN_KM * 1000),),
    )
    out, max_gear = [], None
    for r in rows:
        try:
            s, g, e, b = (json.loads(r[k]) if r[k] else {} for k in ("s", "g", "e", "b"))
        except ValueError:
            continue
        for k in (g or {}).get("seconds_per_gear") or {}:
            i = _gear_int(k)
            if i is not None and (max_gear is None or i > max_gear):
                max_gear = i
        if r["id"] != exclude_pk:
            out.append(_ride_metrics(s or {}, g or {}, e or {}, b or {}, r["ascent_m"]))
    return out, max_gear


def ride_facts(store, ride_pk: int) -> dict | None:
    """Fakten einer Fahrt; ``None`` bei unbekannter oder zu kurzer Fahrt (< 1 km)."""
    ride = store.get_ride(ride_pk)
    if not ride or (ride.get("distance_m") or 0) < MIN_KM * 1000:
        return None
    a = ride["analysis"]
    s = a.get("summary") or {}
    km = s.get("distance_km") or ride["distance_m"] / 1000
    energy, battery = a.get("energy") or {}, a.get("battery") or {}
    others, max_gear = _others(store.db, ride_pk)
    m = _ride_metrics(s, a.get("gears") or {}, energy, battery, ride.get("ascent_m"))
    moving = s.get("moving_time_s") or 0
    assist = a.get("assist") or {}
    hr = (a.get("distributions") or {}).get("heart_rate_s")
    facts = {
        "distance_km": m["distance_km"], "duration_min": m["duration_min"], "moving_min": _r(moving / 60),
        "speed_avg_kmh": m["avg_speed_kmh"], "speed_max_kmh": m["max_speed_kmh"],
        "ascent_m": m["ascent_m"], "descent_m": _r(ride.get("descent_m")),
        "rider_power_avg_w": m["rider_power_avg_w"], "rider_power_max_w": _r(s.get("max_rider_power_w")),
        "motor_power_avg_w": m["motor_power_avg_w"], "motor_power_max_w": _r(s.get("max_motor_power_w")),
        "motor_share_pct": m["motor_share_pct"], "cadence_avg_rpm": m["cadence_avg_rpm"],
        "heart_rate_avg_bpm": m["heart_rate_avg_bpm"], "heart_rate_max_bpm": s.get("max_heart_rate_bpm"),
        "heart_rate_time_pct": _pct_of(hr, sum(hr.values())) if hr else None,
        "minutes_per_assist_mode": {k: _min(v) for k, v in (assist.get("seconds_per_mode") or {}).items() if v},
        "assist_mode_changes": assist.get("changes"),
        "gears": gear_stats(a.get("gears") or {}, km, max_gear),
        "low_cadence_while_pedaling_pct": low_cadence_pct(a.get("series")),
        "by_gradient": [
            {"gradient_pct": label, "minutes": _min(g.get("seconds")), "rider_power_w": g.get("avg_rider_power_w"),
             "motor_power_w": g.get("avg_motor_power_w"), "speed_kmh": g.get("avg_speed_kmh")}
            for label, g in (a.get("by_gradient") or {}).items()
        ],
        "battery": {"start_pct": battery.get("start_pct"), "end_pct": battery.get("end_pct"), "km_per_pct": _r(battery.get("km_per_pct"), 2)},
    }
    if others:
        n = len(others) + 1
        avg = {}
        for k in m:
            vals = [o[k] for o in others if o.get(k) is not None]
            if vals:
                avg[k] = _r(sum(vals) / len(vals), 1)
        rank = {}
        for k in RANK_KEYS:
            if m.get(k) is not None:
                rank[k] = {"position": 1 + sum(1 for o in others if (o.get(k) or 0) > m[k]), "of": n}
        facts["compare"] = {"other_rides_count": len(others), "other_rides_average": avg, "rank": rank}
    return clean(facts)


def _compact_mix(mix: dict) -> dict:
    assist, gears = mix.get("assist") or {}, mix.get("gears") or {}
    return {
        "rides": mix.get("rides"),
        "minutes_per_assist_mode": {k: _min(v) for k, v in (assist.get("seconds_per_mode") or {}).items() if v},
        "assist_mode_changes": assist.get("changes"),
        "per_assist_mode": {k: {"avg_speed_kmh": v.get("avg_moving_speed_kmh"), "motor_power_w": v.get("avg_motor_power_w"),
                                "rider_power_w": v.get("avg_rider_power_w")} for k, v in (assist.get("per_mode") or {}).items()},
        "minutes_per_gear": {k: _min(v) for k, v in (gears.get("seconds_per_gear") or {}).items() if v},
        "gear_changes": gears.get("changes"),
        "avg_cadence_rpm_per_gear": gears.get("avg_cadence_per_gear"),
        "by_gradient": [{"gradient_pct": k, "minutes": _min(v.get("seconds")), "rider_power_w": v.get("avg_rider_power_w"),
                         "motor_power_w": v.get("avg_motor_power_w"), "speed_kmh": v.get("avg_speed_kmh")}
                        for k, v in (mix.get("by_gradient") or {}).items()],
        "time_distributions_pct": {k: _pct_of(v, sum(v.values())) for k, v in (mix.get("distributions") or {}).items() if v},
    }


def period_facts(dash: dict) -> dict | None:
    """Fakten eines Zeitraums aus ``dashboard.build``; ``None`` ohne Fahrten."""
    cur = dash.get("current") or {}
    if not cur.get("rides"):
        return None

    def sums(d: dict) -> dict:
        keys = ("rides", "distance_km", "moving_time_s", "ascent_m", "rider_wh", "motor_wh", "motor_share_pct", "calories_kcal",
                "battery_used_pct", "avg_rider_power_w")
        return {k: d.get(k) for k in keys}

    facts = {
        "period": dash.get("period"),
        "label": cur.get("title") or cur.get("label"),
        "current": sums(cur),
        "previous": sums(dash.get("previous") or {}) if (dash.get("previous") or {}).get("rides") else None,
        "previous_is_partial": dash.get("compare_partial") or None,
        "comparison_pct": {k: v.get("delta_pct") for k, v in (dash.get("comparison") or {}).items() if v.get("delta_pct") is not None},
        "totals_all_time": sums(dash.get("total") or {}),
        "distribution": _compact_mix(dash.get("mix") or {}),
        "bests": [{"key": h.get("key"), "value": h.get("value"), "unit": h.get("unit"), "when": h.get("when_text")}
                  for h in dash.get("highlights") or []],
        "trend": [{"period": s.get("key"), "rides": s.get("rides"), "distance_km": s.get("distance_km"), "ascent_m": s.get("ascent_m"),
                   "rider_wh": s.get("rider_wh"), "motor_share_pct": s.get("motor_share_pct")}
                  for s in (dash.get("series") or [])[-12:]],
    }
    return clean(facts)


# ------------------------------------------------------------------ Zeiträume

COMPARE_MODE = "full"
"""Vorperiode immer ganz vergleichen: Fakten ändern sich nur mit neuen Fahrten, nicht täglich."""

_REF_PERIOD = re.compile(r"^(week|month|year):(\d{4})(?:-W(\d{2})|-(\d{2}))?$")


def period_ref(dash: dict) -> str:
    return "all" if dash.get("period") == "all" else f"{dash['period']}:{dash['current']['key']}"


def build_period(store, ref: str, lang: str, today: date | None = None, compare: str | None = None) -> dict | None:
    """Dashboard-Auswertung zu einer Referenz (``week:2026-W45``, ``month:2026-11``, ``year:2026``, ``all``); sonst ``None``."""
    today = today or datetime.now(store.settings.tz).date()
    compare = compare or COMPARE_MODE
    if ref == "all":
        return store.dashboard("all", 0, today=today, lang=lang, compare_mode=compare)
    m = _REF_PERIOD.match(ref)
    if not m:
        return None
    period, year, week, month = m.group(1), int(m.group(2)), m.group(3), m.group(4)
    try:
        start = date.fromisocalendar(year, int(week), 1) if period == "week" and week else (
            date(year, int(month), 1) if period == "month" and month else (date(year, 1, 1) if period == "year" and not week and not month else None))
    except ValueError:
        return None
    if start is None:
        return None
    offset = dashboard.steps_between(dashboard.period_start(today, period), start, period)
    if offset < 0:  # Fahrt mit Datum in der Zukunft (falsche Display-Uhr): Zeitraum als laufenden behandeln
        today, offset = start, 0
    dash = store.dashboard(period, offset, today=today, lang=lang, compare_mode=compare)
    return dash if period_ref(dash) == ref else None


def ride_period_refs(store, ride_pk: int) -> list[str]:
    row = store.db.one("SELECT start_utc FROM rides WHERE id = ?", (ride_pk,))
    refs = []
    if row and row["start_utc"]:
        d = datetime.fromisoformat(row["start_utc"]).astimezone(store.settings.tz).date()
        refs = [f"{p}:{dashboard.period_key(dashboard.period_start(d, p), p)}" for p in ("week", "month", "year")]
    return refs + ["all"]


def facts_for(store, kind: str, ref: str, lang: str, today: date | None = None) -> dict | None:
    if kind == "ride":
        return ride_facts(store, int(ref)) if ref.isdigit() else None
    dash = build_period(store, ref, lang, today)
    return period_facts(dash) if dash else None


# ------------------------------------------------------------------ Warteschlange und Verarbeitung


class AiQueue:
    """Warteschlange ohne Doppelte (auch nicht für gerade laufende Aufträge); fadensicher."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: deque[tuple[str, str, str]] = deque()
        self._known: set[tuple[str, str, str]] = set()
        self._inflight: tuple[str, str, str] | None = None
        self._scan = False
        self.wake = threading.Event()

    def add(self, kind: str, ref: str, lang: str) -> bool:
        job = (kind, str(ref), lang)
        with self._lock:
            if job in self._known or job == self._inflight:
                return False
            self._known.add(job)
            self._items.append(job)
        self.wake.set()
        return True

    def pop(self) -> tuple[str, str, str] | None:
        with self._lock:
            if not self._items:
                self._inflight = None
                return None
            job = self._items.popleft()
            self._known.discard(job)
            self._inflight = job
            return job

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._known.clear()

    def request_scan(self) -> None:
        with self._lock:
            self._scan = True
        self.wake.set()

    def take_scan(self) -> bool:
        with self._lock:
            scan, self._scan = self._scan, False
            return scan

    def size(self) -> int:
        with self._lock:
            return len(self._items) + (1 if self._inflight else 0)

    def busy(self) -> bool:
        return self.size() > 0


def process(store, job: tuple[str, str, str], call=None, retry_errors: bool = True) -> str:
    """Einen Auftrag abarbeiten. Rückgabe: ``skipped`` (aktuell oder nichts zu tun), ``done`` oder ``error``.

    ``call(messages) -> str`` ersetzt den LLM-Aufruf (Tests). Fehler werden in ``ai_texts`` gespeichert;
    ``FATAL_CODES`` werden zusätzlich weitergereicht (``AiError``), damit der Aufrufer die Schlange leeren kann.
    """
    kind, ref, lang = job
    db = store.db
    if not is_active(db):
        return "skipped"
    facts = facts_for(store, kind, ref, lang)
    if facts is None:
        return "skipped"
    model = model_of(db)
    h = input_hash(facts, model)
    old = store.ai_text(kind, ref, lang)
    if old and old["input_hash"] == h and (old["error"] is None or not retry_errors):
        return "skipped"
    try:
        text = (call or (lambda msgs: chat(db, msgs)))(build_messages(kind, facts, lang))
        content = normalize(parse_json(text), kind)
        if not any(content.values()):
            raise AiError("bad_response", "Antwort ohne Inhalt")
    except AiError as exc:
        store.ai_save(kind, ref, lang, h, None, model, str(exc))
        _set(db, "last_error", str(exc))
        if exc.code in FATAL_CODES:
            raise
        return "error"
    store.ai_save(kind, ref, lang, h, content, model)
    _set(db, "last_error", None)
    return "done"


def ride_pks(store) -> list[int]:
    return [r["id"] for r in store.db.query("SELECT id FROM rides WHERE distance_m >= ? ORDER BY start_utc DESC", (int(MIN_KM * 1000),))]


def all_period_refs(store) -> list[str]:
    refs: dict[str, None] = {"all": None}
    for r in store.db.query("SELECT id FROM rides WHERE start_utc IS NOT NULL AND distance_m >= ?", (int(MIN_KM * 1000),)):
        for ref in ride_period_refs(store, r["id"]):
            refs[ref] = None
    return list(refs)


def enqueue_fill(store, queue: AiQueue, lang: str) -> int:
    """Alle fehlenden oder fehlerhaften oder zu einem anderen Modell gehörenden Texte einreihen (Stand prüft der Worker per Hash)."""
    model = model_of(store.db)
    known = store.ai_known(lang)
    n = 0
    items = [("ride", str(pk)) for pk in ride_pks(store)] + [("period", ref) for ref in all_period_refs(store)]
    for kind, ref in items:
        k = known.get((kind, ref))
        if k is None or k["error"] is not None or k["model"] != model:
            n += queue.add(kind, ref, lang)
    return n


def enqueue_new(store, queue: AiQueue) -> int:
    """Nach neuen Fahrten: Fahrten ≥ 1 km ohne Text einreihen, dazu Woche/Monat/Jahr/Gesamt (Hash prüft der Worker)."""
    lang = lang_of(store.db)
    known = store.ai_known(lang)
    n = 0
    for pk in ride_pks(store):
        if ("ride", str(pk)) in known:
            continue
        n += queue.add("ride", str(pk), lang)
        for ref in ride_period_refs(store, pk):
            n += queue.add("period", ref, lang)
    return n


def lookup(store, queue: AiQueue, kind: str, ref: str, lang: str, facts: dict | None) -> dict:
    """Antwort für ``GET /api/v1/ai/...``: Status, Inhalt, Modell, Zeitpunkt, Fehler; reiht Fehlendes/Veraltetes ein."""
    out = {"status": "off", "content": None, "model": None, "created_at": None, "error": None}
    db = store.db
    if not is_active(db):
        return out
    if facts is None:
        out["status"] = "none"
        return out
    row = store.ai_text(kind, ref, lang)
    h = input_hash(facts, model_of(db))
    if row and row["input_hash"] == h:
        if row["error"]:
            out.update(status="error", error=row["error"], created_at=row["created_at"], model=row["model"])
            return out
        out.update(status="ready", content=row["content"], model=row["model"], created_at=row["created_at"])
        return out
    queue.add(kind, ref, lang)
    if row and row["content"]:
        out.update(status="stale", content=row["content"], model=row["model"], created_at=row["created_at"])
    else:
        out["status"] = "pending"
    return out


def probe(db) -> dict:
    """Kurze Probe-Anfrage; ``{ok, model, reply}`` oder ``AiError``."""
    reply = chat(db, [{"role": "user", "content": "Reply with the single word: ok"}], json_mode=False)
    return {"ok": True, "model": model_of(db), "reply": reply.strip()[:200]}
