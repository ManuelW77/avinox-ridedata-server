"""Ablage und Verarbeitung der Dateien vom Display.

* Rohdateien werden unverändert und inhaltsadressiert gespeichert (``raw/ab/abcdef…``).
* Eine Datei ist über Pfad + Größe eindeutig (Dateizeiten des Displays sind unbrauchbar).
* Jede Fahrt wird beim Eingang einmal vollständig ausgewertet; die Ergebnisse
  liegen als JSON in der Datenbank. Ändert sich ``PARSER_VERSION``, wird beim
  Start alles neu berechnet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from .. import battery
from ..analysis import CUSTOM_MODE_MIN, analyze
from ..buried import Session, device_info, parse_buried
from ..hms import parse_hms
from ..ride_rec import RideRecError, parse_ride
from ..track import to_geojson, to_gpx
from . import dashboard, geocode, i18n
from .firmware import read_app_info
from .config import Settings
from .db import Database

log = logging.getLogger("avinox.store")

PARSER_VERSION = 4
"""Erhöhen, wenn sich Parser oder Auswertung ändern – dann wird alles neu berechnet."""

_SEG = r"[A-Za-z0-9_-]{1,64}"
PATTERNS: dict[str, re.Pattern] = {
    "ride": re.compile(rf"^ride_rec/(?:{_SEG}/)?ride_rec_\d{{1,10}}\.proto$"),
    "index": re.compile(rf"^ride_rec/(?:{_SEG}/)?ride_rec_index\.dat$"),
    "buried": re.compile(r"^buried_rec/buried_file_cur\.json$"),
    "hms": re.compile(r"^hms_rec/hms_current_storage\.proto$"),
}
"""Welche Dateien vom Laufwerk angenommen werden. ``ebike_log_*.dat`` ist verschlüsselt und bleibt draußen."""

UPLOAD_FILES: list[dict[str, str]] = [
    {"kind": "ride", "paths": "ride_rec/ride_rec_<Nr>.proto, ride_rec/<Ordner>/ride_rec_<Nr>.proto", "label": "Fahrtaufzeichnungen"},
    {"kind": "index", "paths": "ride_rec/ride_rec_index.dat, ride_rec/<Ordner>/ride_rec_index.dat", "label": "Fahrten-Index"},
    {"kind": "buried", "paths": "buried_rec/buried_file_cur.json", "label": "Nutzungsstatistik (Akku, Gänge, Modi, Firmware)"},
    {"kind": "hms", "paths": "hms_rec/hms_current_storage.proto", "label": "Meldungen des Antriebs"},
]
"""Anzeige auf der Einstellungsseite; ``kind`` verweist auf ``PATTERNS``."""

NOT_UPLOADED: list[dict[str, str]] = [
    {"paths": "ebike_log_*.dat", "label": "verschlüsselte Systemlogs, zusammen ca. 3 GB"},
    {"paths": "System/", "label": "Index und leere Kartenvorschau"},
    {"paths": "alles andere", "label": "alle übrigen Dateien und Ordner"},
]
"""Anzeige auf der Einstellungsseite: wird nicht hochgeladen."""


class UploadError(ValueError):
    pass


def classify(path: str) -> str | None:
    for kind, pattern in PATTERNS.items():
        if pattern.match(path):
            return kind
    return None


def normalize_path(path: str) -> str:
    """Pfad relativ zur Laufwerkswurzel; Backslashes und führende Teile wie ``/Volumes/X/`` entfernen."""
    p = path.replace("\\", "/").strip().lstrip("/")
    for root in ("ride_rec/", "buried_rec/", "hms_rec/"):
        i = p.find(root)
        if i > 0:
            p = p[i:]
            break
    return p


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def apply_mode_names(analysis: dict, names: dict[int, str]) -> dict:
    """Ersetzt ``eigen_<n>`` durch die eingestellten Namen (Auswertung bleibt gespeichert wie berechnet)."""
    if not names:
        return analysis
    mapping = {f"eigen_{level}": name for level, name in names.items()}
    assist = analysis.get("assist") or {}
    for key in ("seconds_per_mode", "per_mode"):
        if key in assist:
            assist[key] = {mapping.get(k, k): v for k, v in assist[key].items()}
    return analysis


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.raw_dir.mkdir(parents=True, exist_ok=True)
        settings.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self._sessions: list[Session] | None = None
        self._lock = threading.RLock()
        self._derived_cache: dict[tuple[int, str], tuple[str | None, bool | None]] = {}

    # ------------------------------------------------------------------ Firmware

    @property
    def firmware_dir(self) -> Path:
        return self.settings.data_dir / "firmware"

    def _fw_unused_cleanup(self, sha: str) -> None:
        if self.db.one("SELECT 1 FROM firmware WHERE sha256 = ?", (sha,)) is None:
            (self.firmware_dir / f"{sha}.bin").unlink(missing_ok=True)

    def add_firmware(self, data: bytes) -> dict:
        """Speichert ein Image und setzt es als aktuell für sein Projekt (ValueError bei ungültigem Image)."""
        info = read_app_info(data)
        sha = hashlib.sha256(data).hexdigest()
        project, version = info["project_name"], info["version"]
        self.firmware_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.firmware_dir / f"{sha}.tmp"
        tmp.write_bytes(data)
        tmp.replace(self.firmware_dir / f"{sha}.bin")
        with self._lock:
            old = self.db.one("SELECT sha256 FROM firmware WHERE project = ? AND version = ?", (project, version))
            self.db.execute("UPDATE firmware SET is_current = 0 WHERE project = ?", (project,))
            self.db.execute(
                "INSERT INTO firmware(version, project, size, sha256, uploaded_at, is_current) VALUES (?,?,?,?,?,1) "
                "ON CONFLICT(project, version) DO UPDATE SET size = excluded.size, sha256 = excluded.sha256, "
                "uploaded_at = excluded.uploaded_at, is_current = 1",
                (version, project, len(data), sha, _now()),
            )
            if old and old["sha256"] != sha:
                self._fw_unused_cleanup(old["sha256"])
            row = self.db.one("SELECT * FROM firmware WHERE project = ? AND version = ?", (project, version))
        return {**dict(row), "build_date": f"{info['date']} {info['time']}".strip(), "idf_ver": info["idf_ver"]}

    def current_firmware(self, project: str) -> dict | None:
        row = self.db.one("SELECT * FROM firmware WHERE project = ? AND is_current = 1", (project,))
        return dict(row) if row else None

    def list_firmware(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM firmware ORDER BY project, is_current DESC, uploaded_at DESC, id DESC")]

    def get_firmware(self, fw_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM firmware WHERE id = ?", (fw_id,))
        return dict(row) if row else None

    def firmware_by_sha(self, sha: str) -> dict | None:
        row = self.db.one("SELECT * FROM firmware WHERE sha256 = ? LIMIT 1", (sha,))
        return dict(row) if row else None

    def read_firmware(self, sha: str) -> bytes | None:
        path = self.firmware_dir / f"{sha}.bin"
        return path.read_bytes() if path.is_file() else None

    def set_current_firmware(self, fw_id: int) -> bool:
        with self._lock:
            row = self.get_firmware(fw_id)
            if not row:
                return False
            self.db.execute("UPDATE firmware SET is_current = (id = ?) WHERE project = ?", (fw_id, row["project"]))
        return True

    def delete_firmware(self, fw_id: int) -> str:
        """'ok', 'missing' oder 'current' (die aktuelle Version wird nicht gelöscht)."""
        with self._lock:
            row = self.get_firmware(fw_id)
            if not row:
                return "missing"
            if row["is_current"]:
                return "current"
            self.db.execute("DELETE FROM firmware WHERE id = ?", (fw_id,))
            self._fw_unused_cleanup(row["sha256"])
        return "ok"

    # ------------------------------------------------------------------ Dateien

    def _raw_path(self, sha: str) -> Path:
        return self.settings.raw_dir / sha[:2] / sha

    def has_file(self, path: str, size: int) -> bool:
        return self.db.one("SELECT 1 FROM files WHERE path = ? AND size = ?", (path, size)) is not None

    def manifest(self) -> list[dict]:
        return [{"path": r["path"], "size": r["size"]} for r in self.db.query("SELECT path, size FROM files ORDER BY path, size")]

    def add_file(self, path: str, data: bytes) -> dict:
        """Speichert eine Datei und verarbeitet sie. Gibt ``{"file_id", "kind", "new", "ride"}`` zurück."""
        path = normalize_path(path)
        kind = classify(path)
        if kind is None:
            raise UploadError(f"Pfad nicht erlaubt: {path}")
        if len(data) > self.settings.max_upload_bytes:
            raise UploadError("Datei zu groß")
        with self._lock:
            existing = self.db.one("SELECT id FROM files WHERE path = ? AND size = ?", (path, len(data)))
            if existing:
                return {"file_id": existing["id"], "kind": kind, "new": False, "ride": self._ride_id_for_file(existing["id"])}
            sha = hashlib.sha256(data).hexdigest()
            target = self._raw_path(sha)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                tmp = target.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(target)
            cur = self.db.execute(
                "INSERT INTO files(path, size, sha256, kind, received_at) VALUES (?, ?, ?, ?, ?)",
                (path, len(data), sha, kind, _now()),
            )
            file_id = cur.lastrowid
            ride = None
            if kind == "ride":
                ride = self._process_ride(file_id, path, data)
            elif kind == "buried":
                self._sessions = None
                self._refresh_device_stats()
            log.info("Datei angenommen: %s (%d Byte, %s)", path, len(data), kind)
            return {"file_id": file_id, "kind": kind, "new": True, "ride": ride}

    def _ride_id_for_file(self, file_id: int) -> int | None:
        row = self.db.one("SELECT id FROM rides WHERE file_id = ?", (file_id,))
        return row["id"] if row else None

    def read_raw(self, file_id: int) -> bytes:
        row = self.db.one("SELECT sha256 FROM files WHERE id = ?", (file_id,))
        if not row:
            raise FileNotFoundError(file_id)
        return self._raw_path(row["sha256"]).read_bytes()

    # ----------------------------------------------------------- Teil-Uploads

    def _part_path(self, path: str, size: int) -> Path:
        key = hashlib.sha1(f"{path}|{size}".encode()).hexdigest()
        return self.settings.incoming_dir / f"{key}.part"

    def upload_status(self, path: str, size: int) -> dict:
        path = normalize_path(path)
        if self.has_file(path, size):
            return {"path": path, "size": size, "received": size, "complete": True}
        part = self._part_path(path, size)
        return {"path": path, "size": size, "received": part.stat().st_size if part.exists() else 0, "complete": False}

    def upload_chunk(self, path: str, size: int, offset: int, chunk: bytes) -> dict:
        """Wiederaufnehmbarer Upload in Stücken. ``offset`` muss dem bisher Empfangenen entsprechen."""
        path = normalize_path(path)
        if classify(path) is None:
            raise UploadError(f"Pfad nicht erlaubt: {path}")
        if size < 0 or size > self.settings.max_upload_bytes:
            raise UploadError("Ungültige Größe")
        with self._lock:
            if self.has_file(path, size):
                return {"path": path, "size": size, "received": size, "complete": True, "ride": None, "new": False}
            part = self._part_path(path, size)
            received = part.stat().st_size if part.exists() else 0
            if offset != received:
                return {"path": path, "size": size, "received": received, "complete": False, "conflict": True}
            if received + len(chunk) > size:
                raise UploadError("Mehr Daten als angekündigt")
            with part.open("ab") as fh:
                fh.write(chunk)
            received += len(chunk)
            if received < size:
                return {"path": path, "size": size, "received": received, "complete": False}
            data = part.read_bytes()
            part.unlink()
            result = self.add_file(path, data)
            return {"path": path, "size": size, "received": size, "complete": True, "ride": result["ride"], "new": result["new"]}

    # ------------------------------------------------------------------ Fahrten

    def sessions(self) -> list[Session]:
        """Alle Sessions aus allen gespeicherten Statistik-Dateien, doppelte zusammengeführt."""
        with self._lock:
            if self._sessions is None:
                seen: dict[tuple, Session] = {}
                for row in self.db.query("SELECT id FROM files WHERE kind = 'buried' ORDER BY id"):
                    text = self.read_raw(row["id"]).decode("utf-8", "replace")
                    for s in parse_buried(text):
                        first = s.events[0].time_raw if s.events else ""
                        seen[(s.boot_count, first)] = s  # neuere Datei gewinnt
                self._sessions = list(seen.values())
            return self._sessions

    def _process_ride(self, file_id: int, path: str, data: bytes) -> int | None:
        current = self.db.one("SELECT r.id, r.file_id, f.size FROM rides r JOIN files f ON f.id = r.file_id WHERE r.path = ?", (path,))
        if current and current["file_id"] != file_id and current["size"] > len(data):
            return current["id"]  # ältere, kürzere Fassung einer schon bekannten Fahrt
        try:
            ride = parse_ride(data)
        except RideRecError as exc:
            log.warning("Fahrt %s nicht lesbar: %s", path, exc)
            return None
        analysis = analyze(ride, self.sessions())
        s = analysis["summary"]
        row = {
            "path": path,
            "file_id": file_id,
            "ride_id": ride.header.ride_id,
            "start_utc": s["start_time"],
            "duration_s": s["duration_s"],
            "moving_time_s": s["moving_time_s"],
            "distance_m": ride.header.distance_m,
            "ascent_m": s["ascent_m"],
            "descent_m": s["descent_m"],
            "avg_speed_kmh": s["avg_speed_kmh"],
            "max_speed_kmh": s["max_speed_kmh"],
            "avg_rider_power_w": s["avg_rider_power_w"],
            "avg_motor_power_w": s["avg_motor_power_w"],
            "avg_cadence_rpm": s["avg_cadence_rpm"],
            "calories_kcal": s["calories_kcal"],
            "battery_start_pct": s["battery_start_pct"],
            "battery_end_pct": s["battery_end_pct"],
            "has_gps": int(analysis["gps"]["points"] > 1),
            "has_device_stats": int(analysis["device_stats"] is not None),
            "warnings": len(analysis["warnings"]),
            "parser_version": PARSER_VERSION,
            "analysis_json": json.dumps(analysis, ensure_ascii=False, separators=(",", ":")),
            "geojson": json.dumps(to_geojson(ride), separators=(",", ":")),
            "updated_at": _now(),
        }
        cols = ", ".join(row)
        marks = ", ".join(f":{k}" for k in row)
        updates = ", ".join(f"{k} = excluded.{k}" for k in row if k != "path")
        self.db.execute(f"INSERT INTO rides({cols}) VALUES ({marks}) ON CONFLICT(path) DO UPDATE SET {updates}", row)
        return self.db.one("SELECT id FROM rides WHERE path = ?", (path,))["id"]

    def _refresh_device_stats(self) -> None:
        for row in self.db.query("SELECT file_id, path FROM rides WHERE has_device_stats = 0"):
            self._process_ride(row["file_id"], row["path"], self.read_raw(row["file_id"]))

    def reprocess(self, only_outdated: bool = True) -> int:
        """Berechnet Fahrten neu (nach Parser-Update). Gibt die Anzahl zurück."""
        with self._lock:
            self._sessions = None
            sql = "SELECT file_id, path FROM rides" + (" WHERE parser_version < ?" if only_outdated else "")
            rows = self.db.query(sql, (PARSER_VERSION,) if only_outdated else ())
            # Fahrt-Dateien ohne Eintrag (z. B. früher unlesbar) ebenfalls versuchen
            orphans = self.db.query(
                "SELECT f.id AS file_id, f.path FROM files f WHERE f.kind = 'ride' AND NOT EXISTS (SELECT 1 FROM rides r WHERE r.path = f.path)"
            )
            for row in [*rows, *orphans]:
                self._process_ride(row["file_id"], row["path"], self.read_raw(row["file_id"]))
            return len(rows) + len(orphans)

    # ------------------------------------------------------------------ Lesen

    def mode_names(self) -> dict[int, str]:
        return {int(k): v for k, v in (self.db.get_setting("mode_names", {}) or {}).items()}

    def set_mode_names(self, names: dict[int, str]) -> None:
        clean = {str(int(k)): v.strip()[:40] for k, v in names.items() if int(k) >= CUSTOM_MODE_MIN and v and v.strip()}
        self.db.set_setting("mode_names", clean)

    def custom_levels_seen(self) -> list[int]:
        levels: set[int] = set()
        for row in self.db.query("SELECT analysis_json FROM rides"):
            for key in json.loads(row["analysis_json"])["assist"]["seconds_per_mode"]:
                if key.startswith("eigen_"):
                    levels.add(int(key.removeprefix("eigen_")))
        return sorted(levels | set(self.mode_names()))

    def _derived(self, ride_pk: int, updated_at: str, geojson: str | None = None) -> tuple[str | None, bool | None]:
        """(Vorschau-Pfad, Rundfahrt?) je Fahrt und Berechnungsstand, damit nicht jedes Mal GeoJSON geparst wird."""
        key = (ride_pk, updated_at)
        hit = self._derived_cache.get(key)
        if hit is not None:
            return hit
        if geojson is None:
            row = self.db.one("SELECT geojson FROM rides WHERE id = ?", (ride_pk,))
            geojson = row["geojson"] if row else "{}"
        data = json.loads(geojson)
        pts = geocode.route_points(data)
        result = (geocode.thumb_path(data), geocode.is_loop(pts[0], pts[1]) if pts else None)
        for stale in [k for k in self._derived_cache if k[0] == ride_pk]:
            del self._derived_cache[stale]
        self._derived_cache[key] = result
        return result

    def _name_fields(self, row, loop: bool | None) -> dict:
        places = json.loads(row["places_json"]) if row["places_json"] else None
        custom = row["custom_name"] or None
        start_local = datetime.fromisoformat(row["start_utc"]).astimezone(self.settings.tz) if row["start_utc"] else None
        name = custom or geocode.ride_name(places, start_local, bool(loop), i18n.current(), row["ride_id"])
        return {"name": name, "name_custom": custom, "places": places, "is_loop": loop}

    def list_rides(self) -> list[dict]:
        rows = self.db.query(
            "SELECT r.id, r.path, r.ride_id, r.start_utc, r.duration_s, r.moving_time_s, r.distance_m, r.ascent_m, r.descent_m, "
            "r.avg_speed_kmh, r.max_speed_kmh, r.avg_rider_power_w, r.avg_motor_power_w, r.avg_cadence_rpm, r.calories_kcal, "
            "r.battery_start_pct, r.battery_end_pct, r.has_gps, r.has_device_stats, r.warnings, r.updated_at, "
            "m.custom_name, m.places_json FROM rides r LEFT JOIN ride_meta m ON m.path = r.path "
            "ORDER BY r.start_utc IS NULL, r.start_utc DESC, r.path DESC"
        )
        out = []
        for row in rows:
            d = {k: row[k] for k in row.keys() if k not in ("updated_at", "custom_name", "places_json")}
            thumb, loop = self._derived(row["id"], row["updated_at"]) if row["has_gps"] else (None, None)
            d.update(self._name_fields(row, loop))
            d["thumb"] = thumb
            out.append(d)
        return out

    def get_ride(self, ride_pk: int) -> dict | None:
        row = self.db.one(
            "SELECT r.*, m.custom_name, m.places_json FROM rides r LEFT JOIN ride_meta m ON m.path = r.path WHERE r.id = ?", (ride_pk,)
        )
        if not row:
            return None
        hidden = ("analysis_json", "geojson", "custom_name", "places_json")
        out = {k: row[k] for k in row.keys() if k not in hidden}
        out["analysis"] = apply_mode_names(json.loads(row["analysis_json"]), self.mode_names())
        loop = self._derived(row["id"], row["updated_at"], row["geojson"])[1] if row["has_gps"] else None
        out.update(self._name_fields(row, loop))
        return out

    # ------------------------------------------------------------------ Namen

    def set_ride_name(self, ride_pk: int, name: str | None) -> bool:
        row = self.db.one("SELECT path FROM rides WHERE id = ?", (ride_pk,))
        if not row:
            return False
        clean = (name or "").strip()[:80].strip() or None
        self.db.execute(
            "INSERT INTO ride_meta(path, custom_name) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET custom_name = excluded.custom_name",
            (row["path"], clean),
        )
        return True

    def geocode_pending_count(self) -> int:
        row = self.db.one(
            "SELECT COUNT(*) AS n FROM rides r LEFT JOIN ride_meta m ON m.path = r.path WHERE r.has_gps = 1 AND m.geocoded_at IS NULL"
        )
        return row["n"]

    def geocode_pending(self, geocoder, limit: int = 50) -> int:
        """Holt Ortsnamen für Fahrten ohne ``geocoded_at``. Bei Dienstfehler Abbruch (Rest später); Rückgabe = erledigte Fahrten."""
        rows = self.db.query(
            "SELECT r.id, r.path, r.updated_at, r.geojson FROM rides r LEFT JOIN ride_meta m ON m.path = r.path "
            "WHERE r.has_gps = 1 AND m.geocoded_at IS NULL ORDER BY r.start_utc DESC, r.path DESC LIMIT ?",
            (limit,),
        )
        done = 0
        for row in rows:
            pts = geocode.route_points(json.loads(row["geojson"]))
            if pts is None:
                places = None
            else:
                try:
                    names = [self._place_name(geocoder, lat, lon) for lat, lon in pts]
                except geocode.GeocodeError as exc:
                    log.warning("Ortsnamen nicht abrufbar, später erneut: %s", exc)
                    return done
                places = dict(zip(("start", "end", "far"), names))
            self.db.execute(
                "INSERT INTO ride_meta(path, places_json, geocoded_at) VALUES (?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET places_json = excluded.places_json, geocoded_at = excluded.geocoded_at",
                (row["path"], json.dumps(places, ensure_ascii=False) if places else None, _now()),
            )
            done += 1
        return done

    def _place_name(self, geocoder, lat: float, lon: float) -> str | None:
        key = f"{lat:.3f},{lon:.3f}"
        hit = self.db.one("SELECT name FROM geocache WHERE key = ?", (key,))
        if hit:
            return hit["name"]
        name = geocoder.reverse(lat, lon)
        self.db.execute(
            "INSERT INTO geocache(key, name, fetched_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET name = excluded.name, fetched_at = excluded.fetched_at",
            (key, name, _now()),
        )
        return name

    def ride_geojson(self, ride_pk: int) -> str | None:
        row = self.db.one("SELECT geojson FROM rides WHERE id = ?", (ride_pk,))
        return row["geojson"] if row else None

    def ride_gpx(self, ride_pk: int) -> tuple[str, str] | None:
        row = self.db.one("SELECT file_id, ride_id, start_utc FROM rides WHERE id = ?", (ride_pk,))
        if not row:
            return None
        ride = parse_ride(self.read_raw(row["file_id"]))
        name = f"avinox-{row['start_utc'][:10] if row['start_utc'] else 'fahrt'}-{row['ride_id']}.gpx"
        return to_gpx(ride), name

    def ride_raw(self, ride_pk: int) -> tuple[bytes, str] | None:
        row = self.db.one("SELECT file_id, path FROM rides WHERE id = ?", (ride_pk,))
        if not row:
            return None
        return self.read_raw(row["file_id"]), Path(row["path"]).name

    def stats(self) -> dict:
        """Gesamtwerte und Monatswerte über alle Fahrten."""
        rows = self.list_rides()
        total = {
            "rides": len(rows),
            "distance_km": round(sum(r["distance_m"] for r in rows) / 1000, 1),
            "duration_s": sum(r["duration_s"] for r in rows),
            "moving_time_s": sum(r["moving_time_s"] for r in rows),
            "ascent_m": round(sum(r["ascent_m"] or 0 for r in rows)),
            "calories_kcal": round(sum(r["calories_kcal"] or 0 for r in rows)),
        }
        months: dict[str, dict] = {}
        for r in rows:
            if not r["start_utc"]:
                continue
            local = datetime.fromisoformat(r["start_utc"]).astimezone(self.settings.tz)
            m = months.setdefault(local.strftime("%Y-%m"), {"rides": 0, "distance_km": 0.0, "ascent_m": 0.0, "moving_time_s": 0})
            m["rides"] += 1
            m["distance_km"] = round(m["distance_km"] + r["distance_m"] / 1000, 2)
            m["ascent_m"] = round(m["ascent_m"] + (r["ascent_m"] or 0))
            m["moving_time_s"] += r["moving_time_s"]
        return {"total": total, "months": dict(sorted(months.items()))}

    def dashboard(self, period: str = "week", offset: int = 0, today: date | None = None, lang: str = "de") -> dict:
        """Übersicht für die Startseite (siehe ``dashboard.build``). ``today`` nur für Tests."""
        today = today or datetime.now(self.settings.tz).date()
        dash = dashboard.build(dashboard.load_rows(self.db, self.settings.tz), period, offset, today, lang)
        names = self.mode_names()
        if names:
            apply_mode_names(dash["mix"], names)
        return dash

    def device(self) -> dict:
        sessions = self.sessions()
        info = device_info(sessions)
        info["battery_history"] = [
            {"time": s.end_time.isoformat() if s.end_time else None, "boot_count": s.boot_count, **s.battery}
            for s in sessions
            if s.battery
        ]
        battery_in = [
            {"time": s.end_time.isoformat() if s.end_time else None, **s.battery, "temp_s": s.battery_temp_s, "thermal_limit": s.thermal_limit}
            for s in sessions
            if s.battery
        ]
        ride_rows = self.db.query(
            "SELECT start_utc AS time, json_extract(analysis_json, '$.battery.motor_wh_per_pct') AS motor_wh_per_pct, "
            "json_extract(analysis_json, '$.battery.used_pct') AS used_pct FROM rides WHERE start_utc IS NOT NULL AND json_extract(analysis_json, '$.battery.motor_wh_per_pct') IS NOT NULL "
            "ORDER BY start_utc"
        )
        info["battery_health"] = battery.health(battery_in, [dict(r) for r in ride_rows])
        hms = self.db.one("SELECT id FROM files WHERE kind = 'hms' ORDER BY id DESC LIMIT 1")
        info["hms"] = (
            [{"code": e.code, "time": e.time.isoformat() if e.time else None} for e in parse_hms(self.read_raw(hms["id"]))] if hms else []
        )
        info["files"] = self.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM files")
        info["files"] = dict(info["files"])
        return info

    def import_directory(self, root: Path) -> dict:
        """Importiert eine Kopie des Display-Laufwerks (z. B. ``_demo-data``)."""
        counts = {"new": 0, "known": 0, "skipped": 0}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if classify(rel) is None:
                counts["skipped"] += 1
                continue
            result = self.add_file(rel, path.read_bytes())
            counts["new" if result["new"] else "known"] += 1
        return counts

    def close(self) -> None:
        self.db.close()
