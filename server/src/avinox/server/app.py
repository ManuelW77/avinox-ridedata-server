"""FastAPI-App: Upload-API für den ESP32, Lese-API und Weboberfläche."""

from __future__ import annotations

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
        fwd = request.headers.get("x-forwarded-for")
        if fwd and fwd.split(",")[0].strip():
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else None

    def device_header(request: Request) -> str | None:
        value = (request.headers.get("x-avinox-device") or "").strip()
        return value if DEVICE_ID.match(value) else None

    def upload_auth(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not cred or not secrets.compare_digest(cred.credentials, settings.upload_token):
            synclog.event("auth_error", client_ip(request), device_header(request), path=request.url.path, status=401, detail="Token fehlt" if not cred else "Token falsch")
            raise HTTPException(401, "Upload-Token fehlt oder ist falsch", headers={"WWW-Authenticate": "Bearer"})

    def read_auth(cred: HTTPBasicCredentials | None = Depends(basic)) -> None:
        if not settings.web_user:
            return
        ok = cred and secrets.compare_digest(cred.username, settings.web_user) and secrets.compare_digest(cred.password, settings.web_password or "")
        if not ok:
            raise HTTPException(401, "Anmeldung erforderlich", headers={"WWW-Authenticate": 'Basic realm="Avinox"'})

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
        if body.get("event") not in ("heartbeat", "sync"):
            raise HTTPException(400, "event muss 'heartbeat' oder 'sync' sein")
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
        # Nur bekannte Felder speichern
        keep = {k: body[k] for k in ("device_id", "event", "firmware", "uptime_s", "rssi", "ip", "free_heap", "drive") if k in body}
        if isinstance(sync, dict):
            keep["sync"] = {k: sync[k] for k in ("checked", "new", "skipped", "failed", "bytes", "duration_ms", "result", "error") if k in sync}
        ts = synclog.report(keep, client_ip(request))
        return {"ok": True, "server_time": ts}

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
        return page(request, "sync.html", snap=snap)

    @app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def settings_page(request: Request):
        return page(
            request,
            "settings.html",
            levels=store.custom_levels_seen(),
            names=store.mode_names(),
            # Token nur zeigen, wenn die Seite selbst geschützt ist – sonst sähe es jeder im LAN.
            token=settings.upload_token if settings.web_user else None,
            token_hint="AVINOX_UPLOAD_TOKEN" if os.environ.get("AVINOX_UPLOAD_TOKEN") else str(settings.data_dir / "upload_token"),
            patterns=[p.pattern for p in PATTERNS.values()],
            upload_files=UPLOAD_FILES,
            not_uploaded=NOT_UPLOADED,
        )

    return app
