"""FastAPI-App: Upload-API für den ESP32, Lese-API und Weboberfläche."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from starlette.datastructures import UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..analysis import ASSIST_LEVELS
from .config import Settings, load_settings
from .firmware import MAX_FIRMWARE_BYTES
from .dashboard import PERIODS, day_text, emphasize
from .sync_log import DEVICE_ID, SyncLog
from .store import NOT_UPLOADED, PATTERNS, normalize_path, UPLOAD_FILES, Store, UploadError

log = logging.getLogger("avinox.server")
HERE = Path(__file__).parent
MAX_FILES_PER_REQUEST = 50


WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
"""Deutsche Wochentage für ``%a`` – unabhängig von der System-Locale."""


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "–"
    h, rest = divmod(int(seconds), 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def _host_unreachable(host: str) -> bool:
    """True, wenn der ESP32 diesen Host nicht erreichen kann (Loopback, Docker-intern)."""
    name = host.strip()
    if name.startswith("["):
        name = name[1 : name.find("]")].lstrip("[") if "]" in name else name[1:]
    elif name.count(":") == 1:
        name = name.split(":")[0]
    name = name.lower()
    if name in ("localhost", "0.0.0.0", "::1", "::"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return name.endswith(".localhost")
    return ip.is_loopback or ip.is_unspecified or ip in ipaddress.ip_network("172.16.0.0/12")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = Store(settings)
    synclog = SyncLog(store.db)
    redone = store.reprocess(only_outdated=True)
    if redone:
        log.info("%d Fahrten neu berechnet", redone)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            store.close()

    app = FastAPI(title="Avinox Ride Data", lifespan=lifespan, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.store = store
    app.state.synclog = synclog
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    app.state.templates = templates

    def local_time(iso: str | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
        if not iso:
            return "ohne Uhrzeit"
        dt = datetime.fromisoformat(iso).astimezone(settings.tz)
        return dt.strftime(fmt.replace("%a", WEEKDAYS[dt.weekday()]))

    templates.env.filters["duration"] = _fmt_duration
    templates.env.filters["localtime"] = local_time
    templates.env.filters["emph"] = emphasize
    templates.env.filters["day"] = lambda iso: day_text(date.fromisoformat(iso)) if iso else "ohne Datum"
    templates.env.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None
    templates.env.filters["num"] = lambda v, d=0: "–" if v is None else f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")

    bearer = HTTPBearer(auto_error=False)
    basic = HTTPBasic(auto_error=False)

    def client_ip(request: Request) -> str | None:
        # Kein eigenes X-Forwarded-For: das kann jeder Client setzen. Hinter einem Proxy
        # übernimmt uvicorn (--proxy-headers, forwarded_allow_ips) die echte Adresse.
        return request.client.host if request.client else None

    def device_header(request: Request) -> str | None:
        value = (request.headers.get("x-avinox-device") or "").strip()
        return value if DEVICE_ID.match(value) else None

    def upload_auth(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not cred or not secrets.compare_digest(cred.credentials, settings.upload_token):
            # Ohne gültiges Token keine Geräte-ID übernehmen – sonst ließe sich der Gerätestatus fälschen.
            synclog.event("auth_error", client_ip(request), None, path=request.url.path, status=401, detail="Token fehlt" if not cred else "Token falsch")
            raise HTTPException(401, "Upload-Token fehlt oder ist falsch", headers={"WWW-Authenticate": "Bearer"})

    def read_auth(cred: HTTPBasicCredentials | None = Depends(basic)) -> None:
        if not settings.web_user:
            return
        ok = cred and secrets.compare_digest(cred.username, settings.web_user) and secrets.compare_digest(cred.password, settings.web_password or "")
        if not ok:
            raise HTTPException(401, "Anmeldung erforderlich", headers={"WWW-Authenticate": 'Basic realm="Avinox"'})

    FW_ADMIN_HINT = "Firmware-Verwaltung nur mit Web-Passwort (AVINOX_WEB_USER/AVINOX_WEB_PASSWORD) – oder per ‚avinox firmware add‘ auf dem Server"

    def firmware_admin_auth(cred: HTTPBasicCredentials | None = Depends(basic)) -> None:
        """Firmware landet per OTA auf dem ESP32 (Code-Ausführung): nie ohne konfiguriertes Web-Passwort."""
        if not settings.web_user:
            raise HTTPException(403, FW_ADMIN_HINT)
        read_auth(cred)

    def check_length(request: Request, limit: int) -> None:
        """Größe vor dem Lesen prüfen: ohne Content-Length 411, zu groß 413."""
        raw = request.headers.get("content-length")
        if raw is None or not raw.isdigit():
            raise HTTPException(411, "Content-Length fehlt")
        if int(raw) > limit:
            raise HTTPException(413, "Anfrage zu groß")

    async def read_body(request: Request, limit: int) -> bytes:
        """Liest höchstens ``limit`` Byte und bricht darüber ab (falls Content-Length lügt)."""
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > limit:
                raise HTTPException(413, "Anfrage zu groß")
        return bytes(buf)

    def ride_or_404(ride_pk: int) -> dict:
        ride = store.get_ride(ride_pk)
        if not ride:
            raise HTTPException(404, "Fahrt nicht gefunden")
        return ride

    # ------------------------------------------------------------ Upload (ESP32)

    @app.get("/api/v1/manifest", dependencies=[Depends(upload_auth)], tags=["upload"])
    def manifest(request: Request):
        """Bekannte Dateien (Pfad + Größe) und welche Pfade angenommen werden."""
        synclog.event("manifest", client_ip(request), device_header(request), status=200)
        return {"files": store.manifest(), "patterns": {k: p.pattern for k, p in PATTERNS.items()}}

    @app.get("/api/v1/upload/status", dependencies=[Depends(upload_auth)], tags=["upload"])
    def upload_status(path: str, size: int = Query(ge=0)):
        return store.upload_status(path, size)

    @app.put("/api/v1/upload", dependencies=[Depends(upload_auth)], tags=["upload"])
    async def upload(request: Request, path: str, size: int = Query(ge=0), offset: int = Query(0, ge=0)):
        """Rohe Bytes ab ``offset``. Bei falschem Offset kommt 409 mit dem tatsächlich empfangenen Stand."""
        ip, dev = client_ip(request), device_header(request)

        def fail(status: int, msg: str):
            synclog.event("upload_chunk_error", ip, dev, path=path, size=size, status=status, detail=msg)
            raise HTTPException(status, msg)

        limit = min(settings.max_upload_bytes, max(0, size - offset))
        try:
            check_length(request, limit)
            body = await read_body(request, limit)
        except HTTPException as exc:
            fail(exc.status_code, str(exc.detail))
        try:
            result = store.upload_chunk(path, size, offset, body)
        except UploadError as exc:
            fail(400, str(exc))
        if result.get("conflict"):
            synclog.event("upload_chunk_error", ip, dev, path=path, size=size, status=409, detail="Offset korrigiert")
        elif result.get("complete"):
            synclog.event("file_new" if result.get("new") else "file_known", ip, dev, path=result["path"], size=size, status=200)
        return JSONResponse(result, status_code=409 if result.get("conflict") else 200)

    @app.post("/api/v1/device/report", dependencies=[Depends(upload_auth)], tags=["upload"])
    async def device_report(request: Request):
        """Lebenszeichen/Syncbericht des ESP32 (JSON, höchstens 16 KB)."""
        check_length(request, 16 * 1024)
        raw = await read_body(request, 16 * 1024)
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(400, "Kein gültiges JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON-Objekt erwartet")
        did = body.get("device_id")
        if not isinstance(did, str) or not DEVICE_ID.match(did):
            raise HTTPException(400, "device_id: höchstens 64 Zeichen aus A-Z a-z 0-9 _ -")
        if body.get("event") not in ("heartbeat", "sync", "ota"):
            raise HTTPException(400, "event muss 'heartbeat', 'sync' oder 'ota' sein")
        for key, kinds in (("firmware", str), ("ip", str), ("drive", str), ("uptime_s", (int, float)), ("rssi", (int, float)), ("free_heap", (int, float))):
            if body.get(key) is not None and (not isinstance(body[key], kinds) or isinstance(body[key], bool)):
                raise HTTPException(400, f"{key}: falscher Typ")
        if body.get("drive") not in (None, "present", "absent", "error"):
            raise HTTPException(400, "drive: present, absent oder error")
        sync = body.get("sync")
        if sync is not None:
            if not isinstance(sync, dict):
                raise HTTPException(400, "sync: Objekt erwartet")
            for key in ("checked", "new", "skipped", "failed", "bytes", "duration_ms"):
                if sync.get(key) is not None and (not isinstance(sync[key], (int, float)) or isinstance(sync[key], bool)):
                    raise HTTPException(400, f"sync.{key}: Zahl erwartet")
            if sync.get("result") not in (None, "ok", "warn", "error"):
                raise HTTPException(400, "sync.result: ok, warn oder error")
            if sync.get("error") is not None and not isinstance(sync["error"], str):
                raise HTTPException(400, "sync.error: Text erwartet")
        elif body["event"] == "sync":
            raise HTTPException(400, "sync-Ereignis ohne 'sync'")
        ota = body.get("ota")
        if body["event"] == "ota":
            if not isinstance(ota, dict):
                raise HTTPException(400, "ota-Ereignis ohne 'ota'")
            for key in ("from", "to", "error"):
                if ota.get(key) is not None and (not isinstance(ota[key], str) or len(ota[key]) > 200):
                    raise HTTPException(400, f"ota.{key}: Text erwartet (höchstens 200 Zeichen)")
            if ota.get("result") not in ("started", "ok", "failed", "rolled_back"):
                raise HTTPException(400, "ota.result: started, ok, failed oder rolled_back")
        # Nur bekannte Felder speichern
        keep = {k: body[k] for k in ("device_id", "event", "firmware", "uptime_s", "rssi", "ip", "free_heap", "drive") if k in body}
        if isinstance(sync, dict):
            keep["sync"] = {k: sync[k] for k in ("checked", "new", "skipped", "failed", "bytes", "duration_ms", "result", "error") if k in sync}
        if body["event"] == "ota":
            keep["ota"] = {k: ota[k] for k in ("from", "to", "result", "error") if k in ota}
        ts = synclog.report(keep, client_ip(request))
        return {"ok": True, "server_time": ts}

    @app.get("/api/v1/firmware/latest", dependencies=[Depends(upload_auth)], tags=["firmware"])
    def firmware_latest(request: Request, project: str = Query("avinox-sync", max_length=64)):
        """Aktuelle Firmware für ``project`` (404, wenn keine vorhanden)."""
        fw = store.current_firmware(project)
        synclog.event("firmware_check", client_ip(request), device_header(request), status=200 if fw else 404, detail=(fw["version"] if fw else "keine Firmware"))
        if not fw:
            raise HTTPException(404, "Keine Firmware vorhanden")
        return {"version": fw["version"], "size": fw["size"], "sha256": fw["sha256"], "url": f"/api/v1/firmware/{fw['sha256']}.bin"}

    @app.get("/api/v1/firmware/{sha}.bin", dependencies=[Depends(upload_auth)], tags=["firmware"])
    def firmware_download(request: Request, sha: str):
        fw = store.firmware_by_sha(sha) if re.fullmatch(r"[0-9a-f]{64}", sha) else None
        data = store.read_firmware(sha) if fw else None
        synclog.event("firmware_download", client_ip(request), device_header(request), size=len(data) if data else None, status=200 if data else 404, detail=fw["version"] if fw else sha[:12])
        if data is None:
            raise HTTPException(404, "Firmware nicht gefunden")
        return Response(data, media_type="application/octet-stream", headers={"Content-Length": str(len(data))})

    # ------------------------------------------------------------ Firmware-Verwaltung

    @app.post("/api/v1/firmware", dependencies=[Depends(firmware_admin_auth)], tags=["firmware"])
    async def firmware_upload(request: Request):
        check_length(request, MAX_FIRMWARE_BYTES + 1024 * 1024)
        form = await request.form(max_files=1, max_fields=5)
        f = form.get("file")
        if not isinstance(f, UploadFile):
            raise HTTPException(400, "Feld 'file' fehlt")
        data = await f.read(MAX_FIRMWARE_BYTES + 1)
        try:
            fw = store.add_firmware(data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": fw["id"], "version": fw["version"], "project": fw["project"], "size": fw["size"], "sha256": fw["sha256"]}

    @app.post("/api/v1/firmware/{fw_id}/current", dependencies=[Depends(firmware_admin_auth)], tags=["firmware"])
    def firmware_set_current(fw_id: int):
        if not store.set_current_firmware(fw_id):
            raise HTTPException(404, "Firmware nicht gefunden")
        return {"ok": True}

    @app.delete("/api/v1/firmware/{fw_id}", dependencies=[Depends(firmware_admin_auth)], tags=["firmware"])
    def firmware_delete(fw_id: int):
        result = store.delete_firmware(fw_id)
        if result == "missing":
            raise HTTPException(404, "Firmware nicht gefunden")
        if result == "current":
            raise HTTPException(409, "Die aktuelle Firmware kann nicht gelöscht werden")
        return {"ok": True}

    # ------------------------------------------------------------ Lese-API

    @app.get("/api/v1/rides", dependencies=[Depends(read_auth)], tags=["fahrten"])
    def rides():
        return store.list_rides()

    @app.get("/api/v1/rides/{ride_pk}", dependencies=[Depends(read_auth)], tags=["fahrten"])
    def ride(ride_pk: int):
        return ride_or_404(ride_pk)

    @app.get("/api/v1/rides/{ride_pk}/geojson", dependencies=[Depends(read_auth)], tags=["fahrten"])
    def ride_geojson(ride_pk: int):
        text = store.ride_geojson(ride_pk)
        if text is None:
            raise HTTPException(404, "Fahrt nicht gefunden")
        return Response(text, media_type="application/geo+json")

    @app.get("/api/v1/rides/{ride_pk}/gpx", dependencies=[Depends(read_auth)], tags=["fahrten"])
    def ride_gpx(ride_pk: int):
        found = store.ride_gpx(ride_pk)
        if not found:
            raise HTTPException(404, "Fahrt nicht gefunden")
        text, name = found
        return Response(text, media_type="application/gpx+xml", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/v1/rides/{ride_pk}/raw", dependencies=[Depends(read_auth)], tags=["fahrten"])
    def ride_raw(ride_pk: int):
        found = store.ride_raw(ride_pk)
        if not found:
            raise HTTPException(404, "Fahrt nicht gefunden")
        data, name = found
        return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/v1/sync/log", dependencies=[Depends(read_auth)], tags=["upload"])
    def sync_log_api(limit: int = Query(200, ge=1, le=1000)):
        """ESP32-Geräte und die neuesten Sync-Ereignisse."""
        return synclog.snapshot(limit)

    @app.get("/api/v1/stats", dependencies=[Depends(read_auth)], tags=["statistik"])
    def stats():
        return store.stats()

    @app.get("/api/v1/dashboard", dependencies=[Depends(read_auth)], tags=["statistik"])
    def dashboard_api(period: str = Query("week", pattern="^(day|week|month|year)$"), offset: int = Query(0, ge=0, le=10000)):
        """Übersicht: Zeitreihe je Tag/Woche/Monat/Jahr, Vergleich zum Zeitraum davor, Bestwerte, Gesamt."""
        return store.dashboard(period, offset)

    @app.get("/api/v1/device", dependencies=[Depends(read_auth)], tags=["statistik"])
    def device():
        return store.device()

    @app.get("/api/v1/settings/modes", dependencies=[Depends(read_auth)], tags=["einstellungen"])
    def get_modes():
        return {"names": store.mode_names(), "levels": store.custom_levels_seen()}

    @app.put("/api/v1/settings/modes", dependencies=[Depends(read_auth)], tags=["einstellungen"])
    def put_modes(payload: dict[str, str]):
        try:
            store.set_mode_names({int(k): v for k, v in payload.items()})
        except ValueError as exc:
            raise HTTPException(400, "Schlüssel müssen Modusnummern sein") from exc
        return {"names": store.mode_names()}

    @app.post("/api/v1/files", dependencies=[Depends(read_auth)], tags=["upload"])
    async def upload_files(request: Request):
        """Upload aus dem Browser: Dateiname = Pfad auf dem Laufwerk (z. B. per Ordnerauswahl).

        Höchstens ``MAX_FILES_PER_REQUEST`` Dateien und ``max_upload_bytes`` (+ 1 MB Formular-Overhead) je Anfrage.
        """
        ip = client_ip(request)
        try:
            check_length(request, settings.max_upload_bytes + 1024 * 1024)
        except HTTPException as exc:
            synclog.event("upload_chunk_error", ip, None, status=exc.status_code, detail=str(exc.detail))
            raise
        form = await request.form(max_files=MAX_FILES_PER_REQUEST, max_fields=10)
        files = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
        if not files:
            raise HTTPException(400, "Keine Dateien im Feld 'files'")
        results = []
        for f in files:
            data = await f.read(settings.max_upload_bytes + 1)
            try:
                r = store.add_file(f.filename or "", data)
                results.append({"path": f.filename, "ok": True, "new": r["new"], "ride": r["ride"]})
                synclog.event("file_new" if r["new"] else "file_known", ip, None, path=normalize_path(f.filename or ""), size=len(data), status=200, detail="Browser")
            except UploadError as exc:
                synclog.event("upload_chunk_error", ip, None, path=f.filename, size=len(data), status=400, detail=str(exc))
                results.append({"path": f.filename, "ok": False, "error": str(exc)})
        return {"results": results}

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    # ------------------------------------------------------------ Webseite

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"tile_url": settings.tile_url, "tile_attribution": settings.tile_attribution, **ctx})

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def dashboard_page(request: Request, period: str | None = None, offset: int | None = None):
        if period not in PERIODS:
            period = "month" if period is None else "week"
        if offset is None and request.query_params.get("period") is None:
            # Ohne Vorgaben: der Zeitraum der letzten Fahrt (sonst ist die Seite bei älteren Fahrten leer)
            dash = store.dashboard(period, 0)
            if dash["last_offset"]:
                dash = store.dashboard(period, dash["last_offset"])
        else:
            dash = store.dashboard(period, max(0, min(offset or 0, 10000)))
        return page(request, "dashboard.html", dash=dash)

    @app.get("/rides", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def rides_page(request: Request):
        return page(request, "index.html", rides=store.list_rides(), stats=store.stats())

    @app.get("/rides/{ride_pk}", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def ride_page(request: Request, ride_pk: int):
        ride = ride_or_404(ride_pk)
        names = {**{str(k): v for k, v in ASSIST_LEVELS.items()}, **{str(k): v for k, v in store.mode_names().items()}}
        return page(request, "ride.html", ride=ride, a=ride["analysis"], s=ride["analysis"]["summary"], mode_names=names)

    @app.get("/device", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def device_page(request: Request):
        return page(request, "device.html", d=store.device())

    @app.get("/sync", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def sync_page(request: Request):
        snap = synclog.snapshot(200)
        now = datetime.now(UTC)
        for d in snap["devices"]:
            seen = datetime.fromisoformat(d["last_seen"]) if d["last_seen"] else None
            d["age_min"] = int((now - seen).total_seconds() // 60) if seen else None
            d["offline"] = d["age_min"] is None or d["age_min"] > 15
        return page(request, "sync.html", snap=snap, fw_latest=store.current_firmware("avinox-sync"))

    @app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def settings_page(request: Request):
        # Adresse für den ESP32: fest konfiguriert oder die, unter der der Browser den Server gerade erreicht.
        host = request.headers.get("host", "")
        if settings.public_url:
            server_url, unreachable = settings.public_url, False
        else:
            server_url = f"{request.url.scheme}://{host}" if host else ""
            unreachable = not host or _host_unreachable(host)
        return page(
            request,
            "settings.html",
            levels=store.custom_levels_seen(),
            names=store.mode_names(),
            token=settings.upload_token,
            has_web_login=bool(settings.web_user),
            fw_admin=bool(settings.web_user),
            fw_admin_hint=FW_ADMIN_HINT,
            server_url=server_url,
            server_url_unreachable=unreachable,
            token_hint="AVINOX_UPLOAD_TOKEN" if os.environ.get("AVINOX_UPLOAD_TOKEN") else str(settings.data_dir / "upload_token"),
            patterns=[p.pattern for p in PATTERNS.values()],
            firmware=store.list_firmware(),
            upload_files=UPLOAD_FILES,
            not_uploaded=NOT_UPLOADED,
        )

    return app
