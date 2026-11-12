"""FastAPI-App: Upload-API für den ESP32, Lese-API und Weboberfläche."""

from __future__ import annotations

import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..analysis import ASSIST_LEVELS
from .config import Settings, load_settings
from .store import PATTERNS, Store, UploadError

log = logging.getLogger("avinox.server")
HERE = Path(__file__).parent


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
    templates.env.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None
    templates.env.filters["num"] = lambda v, d=0: "–" if v is None else f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")

    bearer = HTTPBearer(auto_error=False)
    basic = HTTPBasic(auto_error=False)

    def upload_auth(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not cred or not secrets.compare_digest(cred.credentials, settings.upload_token):
            raise HTTPException(401, "Upload-Token fehlt oder ist falsch", headers={"WWW-Authenticate": "Bearer"})

    def read_auth(cred: HTTPBasicCredentials | None = Depends(basic)) -> None:
        if not settings.web_user:
            return
        ok = cred and secrets.compare_digest(cred.username, settings.web_user) and secrets.compare_digest(cred.password, settings.web_password or "")
        if not ok:
            raise HTTPException(401, "Anmeldung erforderlich", headers={"WWW-Authenticate": 'Basic realm="Avinox"'})

    def ride_or_404(ride_pk: int) -> dict:
        ride = store.get_ride(ride_pk)
        if not ride:
            raise HTTPException(404, "Fahrt nicht gefunden")
        return ride

    # ------------------------------------------------------------ Upload (ESP32)

    @app.get("/api/v1/manifest", dependencies=[Depends(upload_auth)], tags=["upload"])
    def manifest():
        """Bekannte Dateien (Pfad + Größe) und welche Pfade angenommen werden."""
        return {"files": store.manifest(), "patterns": {k: p.pattern for k, p in PATTERNS.items()}}

    @app.get("/api/v1/upload/status", dependencies=[Depends(upload_auth)], tags=["upload"])
    def upload_status(path: str, size: int = Query(ge=0)):
        return store.upload_status(path, size)

    @app.put("/api/v1/upload", dependencies=[Depends(upload_auth)], tags=["upload"])
    async def upload(request: Request, path: str, size: int = Query(ge=0), offset: int = Query(0, ge=0)):
        """Rohe Bytes ab ``offset``. Bei falschem Offset kommt 409 mit dem tatsächlich empfangenen Stand."""
        body = await request.body()
        try:
            result = store.upload_chunk(path, size, offset, body)
        except UploadError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(result, status_code=409 if result.get("conflict") else 200)

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

    @app.get("/api/v1/stats", dependencies=[Depends(read_auth)], tags=["statistik"])
    def stats():
        return store.stats()

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
    async def upload_files(files: list[UploadFile] = File(...)):
        """Upload aus dem Browser: Dateiname = Pfad auf dem Laufwerk (z. B. per Ordnerauswahl)."""
        results = []
        for f in files:
            data = await f.read()
            try:
                r = store.add_file(f.filename or "", data)
                results.append({"path": f.filename, "ok": True, "new": r["new"], "ride": r["ride"]})
            except UploadError as exc:
                results.append({"path": f.filename, "ok": False, "error": str(exc)})
        return {"results": results}

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return PlainTextResponse("ok")

    # ------------------------------------------------------------ Webseite

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"tile_url": settings.tile_url, "tile_attribution": settings.tile_attribution, **ctx})

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def index(request: Request):
        return page(request, "index.html", rides=store.list_rides(), stats=store.stats())

    @app.get("/rides/{ride_pk}", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def ride_page(request: Request, ride_pk: int):
        ride = ride_or_404(ride_pk)
        names = {**{str(k): v for k, v in ASSIST_LEVELS.items()}, **{str(k): v for k, v in store.mode_names().items()}}
        return page(request, "ride.html", ride=ride, a=ride["analysis"], s=ride["analysis"]["summary"], mode_names=names)

    @app.get("/device", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def device_page(request: Request):
        return page(request, "device.html", d=store.device())

    @app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(read_auth)], include_in_schema=False)
    def settings_page(request: Request):
        return page(
            request,
            "settings.html",
            levels=store.custom_levels_seen(),
            names=store.mode_names(),
            token=settings.upload_token,
            patterns=[p.pattern for p in PATTERNS.values()],
        )

    return app
