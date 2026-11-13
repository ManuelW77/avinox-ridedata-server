"""MCP-Server (nur lesend): Fahrdaten für Claude, ChatGPT & Co.

Authentifizierung ausschließlich über das MCP-Token (Header ``Authorization: Bearer`` oder Pfad
``/mcp/<token>``). Es gehen nie GPS-Daten, Tracks, Seriennummern, Dateinamen, Benutzer, Tokens
oder Schlüssel nach außen (``ai.clean``).
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from . import ai

PATH = "/mcp"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ Einstellungen (Tabelle settings)


def is_enabled(db) -> bool:
    return bool(db.get_setting("mcp_enabled", False)) and bool(db.get_setting("mcp_token"))


def token_of(db) -> str | None:
    return db.get_setting("mcp_token") or None


def set_enabled(db, enabled: bool) -> None:
    if enabled and not token_of(db):
        db.set_setting("mcp_token", secrets.token_urlsafe(32))
    db.set_setting("mcp_enabled", bool(enabled))


def rotate_token(db) -> str:
    token = secrets.token_urlsafe(32)
    db.set_setting("mcp_token", token)
    return token


def settings_view(db, base_url: str) -> dict:
    token = token_of(db)
    url = base_url.rstrip("/") + PATH
    return {
        "enabled": bool(db.get_setting("mcp_enabled", False)),
        "token": token,
        "url": url,
        "url_with_token": f"{url}/{token}" if token else None,
    }


# ------------------------------------------------------------------ ASGI-Tor


class McpGate:
    """Reines ASGI-Tor vor der Web-App: umgeht Login/CSRF/Origin-Prüfung, prüft nur das MCP-Token."""

    def __init__(self, app, mcp_app, db):
        self.app, self.mcp_app, self.db = app, mcp_app, db

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not (path == PATH or path.startswith(PATH + "/")):
            return await self.app(scope, receive, send)
        given = path[len(PATH) + 1:].strip("/") if path.startswith(PATH + "/") else ""
        if not given:
            auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
            scheme, _, value = auth.partition(" ")
            given = value.strip() if scheme.lower() == "bearer" else ""
        # Pfad in-place kürzen (nicht kopieren): uvicorn loggt sonst den Pfad samt Token im Zugriffslog.
        scope["path"] = PATH
        scope["raw_path"] = PATH.encode()
        if not is_enabled(self.db):
            return await _reply(send, 404, {"error": "not found"})
        expected = (token_of(self.db) or "").encode()
        if not given or not secrets.compare_digest(given.encode(), expected):
            return await _reply(send, 401, {"error": "unauthorized"}, [(b"www-authenticate", b"Bearer")])
        await self.mcp_app(scope, receive, send)


async def _reply(send, status: int, body: dict, headers: list | None = None) -> None:
    raw = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()), *(headers or [])]})
    await send({"type": "http.response.body", "body": raw})


# ------------------------------------------------------------------ Werkzeuge


def _shrink(obj, keep: int = 12):
    """Lange Listen auf die letzten Einträge kürzen."""
    if isinstance(obj, dict):
        return {k: _shrink(v, keep) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shrink(v, keep) for v in obj[-keep:]]
    return obj


def _text(obj) -> str:
    return json.dumps(ai.clean(obj), ensure_ascii=False)


def _local_midnight(value: str | None, name: str, tz) -> datetime | None:
    if value is None:
        return None
    if not _DATE.match(value):
        raise ValueError(f"{name} must be YYYY-MM-DD")
    try:
        return datetime.combine(date.fromisoformat(value), time.min, tzinfo=tz)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid date (YYYY-MM-DD)") from exc


def _ai_text(store, kind: str, ref: str) -> dict | None:
    row = store.ai_text(kind, ref, ai.lang_of(store.db))
    if row and row.get("content") and not row.get("error"):
        c = row["content"]
        return ai.clean(c) if isinstance(c, (dict, list)) else c
    return None


def build(store):
    db, tz = store.db, store.settings.tz
    mcp = MCPServer(
        "avinox",
        instructions=(
            "Read-only access to e-bike ride data recorded by a DJI Avinox drive system. "
            "Start with get_period or list_rides, then get_ride for details. Power is in watts, energy in Wh, "
            "distances in km. No GPS data or serial numbers are available."
        ),
    )

    @mcp.tool()
    def list_rides(
        limit: Annotated[int, Field(ge=1, le=200, description="Number of rides to return (1-200)")] = 20,
        offset: Annotated[int, Field(ge=0, le=100000, description="Rides to skip, for paging")] = 0,
        date_from: Annotated[str | None, Field(description="Only rides on or after this local date, YYYY-MM-DD")] = None,
        date_to: Annotated[str | None, Field(description="Only rides on or before this local date, YYYY-MM-DD")] = None,
    ) -> str:
        """List recorded e-bike rides, newest first. Returns id (use with get_ride), local start time, name, distance (km),
        ride time (minutes), ascent (m), rider and motor energy (Wh) and the motor share of total energy (%)."""
        lo = _local_midnight(date_from, "date_from", tz)
        hi = _local_midnight(date_to, "date_to", tz)
        where, args = ["r.start_utc IS NOT NULL"], []
        if lo:
            where.append("r.start_utc >= ?")
            args.append(lo.astimezone(timezone.utc).isoformat())
        if hi:
            where.append("r.start_utc < ?")
            args.append((hi + timedelta(days=1)).astimezone(timezone.utc).isoformat())
        cond = " AND ".join(where)
        total = db.one(f"SELECT COUNT(*) AS n FROM rides r WHERE {cond}", tuple(args))["n"]
        rows = db.query(
            "SELECT r.id, r.ride_id, r.start_utc, r.duration_s, r.moving_time_s, r.distance_m, r.ascent_m, "
            "json_extract(r.analysis_json, '$.energy') AS energy, m.custom_name, m.places_json "
            f"FROM rides r LEFT JOIN ride_meta m ON m.path = r.path WHERE {cond} "
            "ORDER BY r.start_utc DESC, r.path DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        out = []
        for row in rows:
            energy = json.loads(row["energy"]) if row["energy"] else {}
            start = datetime.fromisoformat(row["start_utc"]).astimezone(tz)
            rider_kj = energy.get("rider_kj")
            item = {
                "id": row["id"], "start_local": start.strftime("%Y-%m-%d %H:%M"), "name": store._name_fields(row, None)["name"],
                "distance_km": round((row["distance_m"] or 0) / 1000, 1),
                "ride_time_min": round((row["moving_time_s"] or row["duration_s"] or 0) / 60),
                "ascent_m": round(row["ascent_m"]) if row["ascent_m"] is not None else None,
                "rider_wh": round(rider_kj / 3.6, 1) if rider_kj is not None else None,
                "motor_wh": energy.get("motor_wh"), "motor_share_pct": energy.get("motor_share_pct"),
            }
            out.append({k: v for k, v in item.items() if v is not None})
        return json.dumps({"total": total, "offset": offset, "rides": out}, ensure_ascii=False)

    @mcp.tool()
    def get_ride(ride_id: Annotated[int, Field(ge=1, le=2**31, description="Ride id from list_rides")]) -> str:
        """Detailed figures of one ride (speed, power, cadence, heart rate, gears, assist modes, gradient bands, battery)
        plus a comparison with all other rides (averages and rank), the ride name and date, and the AI-written text for
        the ride if one exists. Rides under 1 km have no detailed figures."""
        ride = store.get_ride(ride_id)
        if not ride:
            raise ValueError("ride not found")
        facts = ai.ride_facts(store, ride_id)
        start = datetime.fromisoformat(ride["start_utc"]).astimezone(tz).strftime("%Y-%m-%d %H:%M") if ride.get("start_utc") else None
        out = {
            "id": ride_id, "name": ride["name"], "start_local": start,
            "figures": facts if facts is not None else "ride too short (under 1 km) for detailed figures",
            "ai_text": _ai_text(store, "ride", str(ride_id)),
        }
        return json.dumps({k: v for k, v in out.items() if v is not None}, ensure_ascii=False)

    @mcp.tool()
    def get_period(
        period: Annotated[str, Field(pattern="^(week|month|year|all)$", description="week, month, year or all")] = "week",
        offset: Annotated[int, Field(ge=0, le=1000, description="0 = current period, 1 = the one before, ...")] = 0,
    ) -> str:
        """Summary of a time period: ride count, distance, time, ascent, rider/motor energy, motor share, comparison with
        the previous period, distribution of assist modes, best values and a trend of recent periods.
        Includes the AI-written text for the period if one exists."""
        dash = store.dashboard(period, 0 if period == "all" else offset, lang=ai.lang_of(db), compare_mode=ai.COMPARE_MODE)
        facts = ai.period_facts(dash)
        label = (dash.get("current") or {}).get("title") or (dash.get("current") or {}).get("label")
        if facts is None:
            return json.dumps({"period": period, "label": label, "rides": 0}, ensure_ascii=False)
        return _text({**facts, "label": label, "ai_text": _ai_text(store, "period", ai.period_ref(dash))})

    @mcp.tool()
    def get_bests() -> str:
        """Personal best values over all rides (longest ride, most ascent, top speed, highest power, ...), each with
        value, unit, date and the ride id (use with get_ride)."""
        dash = store.dashboard("all", 0, lang=ai.lang_of(db), compare_mode=ai.COMPARE_MODE)
        bests = []
        for h in dash.get("highlights") or []:
            item = ai.clean({"title": h.get("title"), "value": h.get("value"), "unit": h.get("unit"),
                             "when": h.get("when_text"), "summary": h.get("sentence")})
            if h.get("ride"):
                item["ride_id"] = h["ride"]
            bests.append(item)
        return json.dumps({"bests": bests}, ensure_ascii=False)

    @mcp.tool()
    def get_bike_status() -> str:
        """Battery and drive-unit status: charge cycles, power-on count, latest battery readings (charge, temperature),
        a battery health summary and recent error codes. Contains no serial numbers or identifiers."""
        dev = store.device()
        hist = dev.get("battery_history") or []
        return _text({
            "charge_cycles": dev.get("charge_cycles"), "boot_count": dev.get("boot_count"),
            "latest_battery_session": hist[-1] if hist else None,
            "battery_health": _shrink(dev.get("battery_health")),
            "recent_error_codes": [e["code"] for e in (dev.get("hms") or [])][-10:],
        })

    return mcp


def create(store):
    """MCP-App (Streamable HTTP, zustandslos, JSON-Antworten) am Pfad ``/mcp``."""
    return build(store).streamable_http_app(
        streamable_http_path=PATH, json_response=True, stateless_http=True,
        # DNS-Rebinding-Schutz unnötig: Zugriff nur mit Token, der Host kann beliebig sein (Proxy, Tunnel).
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
