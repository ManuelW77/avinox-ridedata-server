"""Ablage und Verarbeitung der Dateien vom Display.

* Rohdateien werden unverändert und inhaltsadressiert gespeichert (``raw/ab/abcdef…``).
* Eine Datei ist über Pfad + Größe eindeutig (Dateizeiten des Displays sind unbrauchbar).
* Jede Fahrt wird beim Eingang einmal vollständig ausgewertet; die Ergebnisse
  liegen als JSON in der Datenbank. Ändert sich ``PARSER_VERSION``, wird beim
  Start alles neu berechnet.
* Gelöschte Fahrten bleiben als Vermerk (``deleted_rides``) und in ``files`` erhalten,
  damit sie beim nächsten Sync und bei der Neuberechnung nicht wieder auftauchen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .. import battery, formatwatch
from ..analysis import CUSTOM_MODE_MIN, analyze, assist_name
from ..buried import Session, device_info, parse_buried
from ..hms import parse_hms
from ..merge import merge_rides, suggest_pairs
from .. import physics
from ..ride_rec import RideRecError, parse_ride
from ..fit import to_fit
from ..track import to_geojson, to_gpx
from . import dashboard, geocode, healthlog, i18n
from .firmware import read_app_info
from .config import Settings
from .db import Database

log = logging.getLogger("avinox.store")

PARSER_VERSION = 7
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


class PhysicsSetupError(ValueError):
    """Für die Fahrphysik fehlt das Systemgewicht in den Einstellungen."""


class UploadError(ValueError):
    pass


class MergeError(ValueError):
    """Zusammenführen nicht möglich; ``code``: not_found | too_few | no_start | unreadable."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


MERGE_PREFIX = "merge:"
"""Pfad-Präfix der synthetischen Zeile einer zusammengeführten Fahrt in ``rides``."""


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
    for key in ("seconds_per_mode", "moving_seconds_per_mode", "per_mode"):
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

    def firmware_by_project_version(self, project: str, version: str) -> dict | None:
        row = self.db.one("SELECT * FROM firmware WHERE project = ? AND version = ?", (project, version))
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
            if kind in ("ride", "buried", "hms"):
                try:
                    self._record_findings(formatwatch.check(kind, path, data), path)
                except Exception:  # der Wächter darf den Upload nie scheitern lassen
                    log.exception("Format-Wächter fehlgeschlagen: %s", path)
            log.info("Datei angenommen: %s (%d Byte, %s)", path, len(data), kind)
            return {"file_id": file_id, "kind": kind, "new": True, "ride": ride}

    # ------------------------------------------------------------ Format-Wächter

    def _record_findings(self, findings, path: str) -> None:
        now = _now()
        for f in findings:
            self.db.execute(
                "INSERT INTO format_findings(kind, key, detail, file_path, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, key) DO UPDATE SET count = count + 1, last_seen = excluded.last_seen",
                (f.kind, f.key, f.detail, path, now, now),
            )

    def format_findings(self, limit: int = 200) -> list[dict]:
        rows = self.db.query("SELECT * FROM format_findings ORDER BY last_seen DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def format_findings_unseen(self) -> dict:
        """Ungesehene Funde je Gruppe: ``{"format": {"count", "max_id"}, "hms": {...}}``."""
        out = {"format": {"count": 0, "max_id": 0}, "hms": {"count": 0, "max_id": 0}}
        for r in self.db.query(
            "SELECT kind = 'hms' AS is_hms, COUNT(*) AS n, MAX(id) AS m FROM format_findings WHERE seen = 0 GROUP BY is_hms"
        ):
            out["hms" if r["is_hms"] else "format"] = {"count": r["n"], "max_id": r["m"] or 0}
        return out

    def mark_findings_seen(self) -> None:
        self.db.execute("UPDATE format_findings SET seen = 1 WHERE seen = 0")

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

    def _load_ride(self, path: str, data: bytes):
        """Fahrt parsen; ohne Gurt-Puls den Puls aus HealthLog (``ride_hr``) einsetzen. Gibt (Ride, Pulsquelle) zurück.

        Pulsquelle: ``belt``, ``healthlog_workout|measurements|mixed`` oder ``None``.
        """
        ride = parse_ride(data)
        if healthlog.has_belt_hr(ride):
            return ride, "belt"
        row = self.db.one("SELECT source, points_json FROM ride_hr WHERE path = ? AND status = 'ok'", (path,))
        if row and row["points_json"] and healthlog.merge_heart_rate(ride, json.loads(row["points_json"])):
            return ride, row["source"]
        return ride, None

    def _process_ride(self, file_id: int, path: str, data: bytes) -> int | None:
        if self.db.one("SELECT 1 FROM deleted_rides WHERE path = ?", (path,)):
            return None  # vom Nutzer gelöscht: nur in files vermerkt
        group = self._merge_group_of(path)
        if group:  # Teil einer zusammengeführten Fahrt: Gruppe neu aufbauen
            return self._rebuild_merge(group)
        current = self.db.one("SELECT r.id, r.file_id, f.size FROM rides r JOIN files f ON f.id = r.file_id WHERE r.path = ?", (path,))
        if current and current["file_id"] != file_id and current["size"] > len(data):
            return current["id"]  # ältere, kürzere Fassung einer schon bekannten Fahrt
        try:
            ride, hr_source = self._load_ride(path, data)
        except RideRecError as exc:
            log.warning("Fahrt %s nicht lesbar: %s", path, exc)
            return None
        return self._store_ride(file_id, path, ride, hr_source)

    def _store_ride(self, file_id: int, path: str, ride, hr_source: str | None) -> int:
        """Wertet eine (ggf. zusammengeführte) Fahrt aus und schreibt die Zeile in ``rides``."""
        analysis = analyze(ride, self.sessions())
        analysis["hr_source"] = hr_source
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
        for row in self.db.query(f"SELECT file_id, path FROM rides WHERE has_device_stats = 0 AND path NOT LIKE '{MERGE_PREFIX}%'"):
            self._process_ride(row["file_id"], row["path"], self.read_raw(row["file_id"]))

    def reprocess(self, only_outdated: bool = True) -> int:
        """Berechnet Fahrten neu (nach Parser-Update). Gibt die Anzahl zurück."""
        with self._lock:
            self._sessions = None
            sql = f"SELECT file_id, path FROM rides WHERE path NOT LIKE '{MERGE_PREFIX}%'" + (" AND parser_version < ?" if only_outdated else "")
            rows = self.db.query(sql, (PARSER_VERSION,) if only_outdated else ())
            # Fahrt-Dateien ohne Eintrag (z. B. früher unlesbar) ebenfalls versuchen
            orphans = self.db.query(
                "SELECT f.id AS file_id, f.path FROM files f WHERE f.kind = 'ride' AND NOT EXISTS (SELECT 1 FROM rides r WHERE r.path = f.path) "
                "AND f.path NOT IN (SELECT path FROM deleted_rides) "
                "AND f.path NOT IN (SELECT value FROM ride_merges, json_each(member_paths_json))"
            )
            for row in [*rows, *orphans]:
                self._process_ride(row["file_id"], row["path"], self.read_raw(row["file_id"]))
            merges = 0
            for g in self.db.query(
                "SELECT m.group_path, r.parser_version FROM ride_merges m LEFT JOIN rides r ON r.path = m.group_path"
            ):
                if not only_outdated or g["parser_version"] is None or g["parser_version"] < PARSER_VERSION:
                    self._rebuild_merge(g["group_path"])
                    merges += 1
            return len(rows) + len(orphans) + merges

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
            "json_extract(r.analysis_json, '$.summary.elapsed_s') AS elapsed_s, "
            "(SELECT json_array_length(g.member_paths_json) FROM ride_merges g WHERE g.group_path = r.path) AS merged_count, "
            "m.custom_name, m.places_json FROM rides r LEFT JOIN ride_meta m ON m.path = r.path "
            "ORDER BY r.start_utc IS NULL, r.start_utc DESC, r.path DESC"
        )
        hints = suggest_pairs([{"id": r["id"], "start": r["start_utc"], "elapsed_s": r["elapsed_s"]} for r in rows])
        out = []
        for row in rows:
            d = {k: row[k] for k in row.keys() if k not in ("updated_at", "custom_name", "places_json", "elapsed_s")}
            d["merged_count"] = row["merged_count"] or 0
            d["merge_prev"] = hints.get(row["id"])
            thumb, loop = self._derived(row["id"], row["updated_at"]) if row["has_gps"] else (None, None)
            d.update(self._name_fields(row, loop))
            d["thumb"] = thumb
            out.append(d)
        names = {r["id"]: r["name"] for r in out}
        for d in out:
            d["merge_prev_name"] = names.get(d["merge_prev"])
        return out

    def get_ride(self, ride_pk: int) -> dict | None:
        row = self.db.one(
            "SELECT r.*, m.custom_name, m.places_json, "
            "(SELECT json_array_length(g.member_paths_json) FROM ride_merges g WHERE g.group_path = r.path) AS merged_count "
            "FROM rides r LEFT JOIN ride_meta m ON m.path = r.path WHERE r.id = ?", (ride_pk,)
        )
        if not row:
            return None
        hidden = ("analysis_json", "geojson", "custom_name", "places_json", "merged_count")
        out = {k: row[k] for k in row.keys() if k not in hidden}
        out["merged_count"] = row["merged_count"] or 0
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

    def delete_ride(self, ride_pk: int) -> bool:
        """Löscht eine Fahrt samt Rohdaten; der Pfad wird gesperrt, die files-Zeilen bleiben (Manifest)."""
        with self._lock:
            row = self.db.one("SELECT path FROM rides WHERE id = ?", (ride_pk,))
            if not row:
                return False
            path = row["path"]
            members = self._merge_members(path)  # zusammengeführte Fahrt: alle Teile löschen
            if members is not None:
                self.db.execute("DELETE FROM ride_merges WHERE group_path = ?", (path,))
                self.db.execute("DELETE FROM ride_meta WHERE path = ?", (path,))
            self._drop_ride_row(ride_pk)
            for member in members if members is not None else [path]:
                self.db.execute("INSERT OR REPLACE INTO deleted_rides(path, deleted_at) VALUES (?, ?)", (member, _now()))
                self.db.execute("DELETE FROM ride_meta WHERE path = ?", (member,))
                self.db.execute("DELETE FROM ride_hr WHERE path = ?", (member,))
            self._sessions = None
            for member in members if members is not None else [path]:
                for f in self.db.query("SELECT DISTINCT sha256 FROM files WHERE path = ?", (member,)):
                    shared = self.db.one(
                        "SELECT 1 FROM files WHERE sha256 = ? AND path != ? AND path NOT IN (SELECT path FROM deleted_rides)",
                        (f["sha256"], member),
                    )
                    if not shared:
                        self._raw_path(f["sha256"]).unlink(missing_ok=True)
            return True

    def _drop_ride_row(self, ride_pk: int) -> None:
        """Entfernt die Zeile in ``rides`` samt KI-Text und Zwischenspeichern (Physik fällt per CASCADE)."""
        self.db.execute("DELETE FROM rides WHERE id = ?", (ride_pk,))
        self.db.execute("DELETE FROM ai_texts WHERE kind = 'ride' AND ref = ?", (str(ride_pk),))
        for key in [k for k in self._derived_cache if k[0] == ride_pk]:
            del self._derived_cache[key]

    # ------------------------------------------------------------- Zusammenführen

    def _merge_members(self, group_path: str) -> list[str] | None:
        row = self.db.one("SELECT member_paths_json FROM ride_merges WHERE group_path = ?", (group_path,))
        return json.loads(row["member_paths_json"]) if row else None

    def _merge_group_of(self, path: str) -> str | None:
        row = self.db.one("SELECT group_path FROM ride_merges, json_each(member_paths_json) WHERE value = ?", (path,))
        return row["group_path"] if row else None

    def _best_file(self, path: str):
        """Größte (= vollständigste) Datei zu einem Fahrtpfad."""
        return self.db.one("SELECT id FROM files WHERE path = ? ORDER BY size DESC, id DESC LIMIT 1", (path,))

    def _load_merged(self, group_path: str):
        """Zusammengeführte Fahrt aus den Teildateien. Gibt (Ride, Pulsquelle) zurück."""
        members = self._merge_members(group_path) or []
        parts = []
        for path in members:
            f = self._best_file(path)
            if not f:
                raise MergeError("unreadable")
            try:
                parts.append(self._load_ride(path, self.read_raw(f["id"])))
            except RideRecError as exc:
                raise MergeError("unreadable") from exc
        parts.sort(key=lambda p: p[0].header.start)
        sources = {src for _, src in parts}
        if "belt" in sources:
            source = "belt"
        elif None in sources:
            source = None if sources == {None} else "healthlog_mixed"
        else:
            source = sources.pop() if len(sources) == 1 else "healthlog_mixed"
        return merge_rides([r for r, _ in parts]), source

    def _rebuild_merge(self, group_path: str) -> int | None:
        """Berechnet die Zeile einer zusammengeführten Fahrt neu (Teile bleiben unverändert in files)."""
        try:
            ride, source = self._load_merged(group_path)
        except MergeError as exc:
            log.warning("Zusammengeführte Fahrt %s nicht lesbar: %s", group_path, exc.code)
            return None
        first = self._best_file(self._merge_members(group_path)[0])
        pk = self._store_ride(first["id"], group_path, ride, source)
        # Ortsnamen neu holen lassen (Strecke kann sich durch eine neuere Teildatei geändert haben)
        self.db.execute(
            "INSERT INTO ride_meta(path, geocoded_at) VALUES (?, NULL) ON CONFLICT(path) DO UPDATE SET geocoded_at = NULL",
            (group_path,),
        )
        return pk

    def merge_rides(self, ride_pks: list[int]) -> int:
        """Führt Fahrten zu einer zusammen und gibt deren ID zurück. Schon zusammengeführte Fahrten in der
        Auswahl werden aufgelöst und mit ihren Teilen neu zusammengesetzt. Wirft ``MergeError``."""
        with self._lock:
            members: set[str] = set()
            old_groups: list[str] = []
            for pk in dict.fromkeys(ride_pks):
                row = self.db.one("SELECT path, start_utc FROM rides WHERE id = ?", (pk,))
                if not row:
                    raise MergeError("not_found")
                parts = self._merge_members(row["path"])
                if parts is None:
                    if not row["start_utc"]:
                        raise MergeError("no_start")
                    members.add(row["path"])
                else:
                    old_groups.append(row["path"])
                    members.update(parts)
            if len(members) < 2:
                raise MergeError("too_few")
            ordered = self._order_paths(members)
            group = MERGE_PREFIX + ordered[0]
            custom = self.db.one("SELECT custom_name FROM ride_meta WHERE path = ?", (ordered[0],))
            self._dissolve([*old_groups])
            for path in ordered:
                row = self.db.one("SELECT id FROM rides WHERE path = ?", (path,))
                if row:
                    self._drop_ride_row(row["id"])
            self.db.execute(
                "INSERT OR REPLACE INTO ride_merges(group_path, member_paths_json, created_at) VALUES (?, ?, ?)",
                (group, json.dumps(ordered), _now()),
            )
            self.db.execute(
                "INSERT INTO ride_meta(path, custom_name) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET custom_name = excluded.custom_name, "
                "places_json = NULL, geocoded_at = NULL",
                (group, custom["custom_name"] if custom else None),
            )
            pk = self._rebuild_merge(group)
            if pk is None:  # nicht lesbar: alles zurücknehmen
                self.split_group(group)
                raise MergeError("unreadable")
            return pk

    def _order_paths(self, paths) -> list[str]:
        """Pfade nach Startzeit der Fahrt sortieren (Startzeit aus der Datei)."""
        keyed = []
        for path in paths:
            f = self._best_file(path)
            try:
                keyed.append((parse_ride(self.read_raw(f["id"])).header.start, path))
            except (RideRecError, TypeError) as exc:
                raise MergeError("unreadable") from exc
        return [p for _, p in sorted(keyed)]

    def _dissolve(self, group_paths: list[str]) -> None:
        for g in group_paths:
            row = self.db.one("SELECT id FROM rides WHERE path = ?", (g,))
            if row:
                self._drop_ride_row(row["id"])
            self.db.execute("DELETE FROM ride_merges WHERE group_path = ?", (g,))
            self.db.execute("DELETE FROM ride_meta WHERE path = ?", (g,))

    def split_group(self, group_path: str) -> list[int]:
        """Löst eine Gruppe auf und berechnet die Teilfahrten aus den Dateien neu."""
        with self._lock:
            members = self._merge_members(group_path) or []
            self._dissolve([group_path])
            ids = []
            for path in members:
                if self.db.one("SELECT 1 FROM deleted_rides WHERE path = ?", (path,)):
                    continue
                f = self._best_file(path)
                if f:
                    pk = self._process_ride(f["id"], path, self.read_raw(f["id"]))
                    if pk:
                        ids.append(pk)
            return ids

    def split_ride(self, ride_pk: int) -> list[int] | None:
        """Trennt eine zusammengeführte Fahrt wieder. ``None`` = Fahrt unbekannt oder nicht zusammengeführt."""
        row = self.db.one("SELECT path FROM rides WHERE id = ?", (ride_pk,))
        if not row or self._merge_members(row["path"]) is None:
            return None
        return self.split_group(row["path"])

    # ------------------------------------------------------------------ KI-Texte

    def ai_text(self, kind: str, ref: str, lang: str) -> dict | None:
        row = self.db.one("SELECT * FROM ai_texts WHERE kind = ? AND ref = ? AND lang = ?", (kind, ref, lang))
        if not row:
            return None
        d = dict(row)
        try:
            d["content"] = json.loads(row["content"]) if row["content"] else None
        except ValueError:
            d["content"] = None
        return d

    def ai_save(self, kind: str, ref: str, lang: str, input_hash: str, content: dict | None, model: str | None, error: str | None = None) -> None:
        """Erfolg (``error=None``) ersetzt den Text; bei Fehler bleibt der alte Text erhalten."""
        old = self.ai_text(kind, ref, lang)
        if content is None and old:
            content, model = old["content"], old["model"]
        self.db.execute(
            "INSERT OR REPLACE INTO ai_texts(kind, ref, lang, input_hash, content, model, created_at, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, ref, lang, input_hash, json.dumps(content, ensure_ascii=False) if content is not None else None, model, _now(), error),
        )

    def ai_counts(self) -> dict:
        row = self.db.one("SELECT SUM(error IS NULL AND content IS NOT NULL) AS done, SUM(error IS NOT NULL) AS errors FROM ai_texts")
        return {"done": int(row["done"] or 0), "errors": int(row["errors"] or 0)}

    def ai_known(self, lang: str) -> dict[tuple[str, str], dict]:
        """(kind, ref) -> {model, error} aller Texte einer Sprache."""
        return {(r["kind"], r["ref"]): {"model": r["model"], "error": r["error"]}
                for r in self.db.query("SELECT kind, ref, model, error FROM ai_texts WHERE lang = ?", (lang,))}

    # ------------------------------------------------------------------ HealthLog-Puls

    HR_RETRY_NONE_S = 3600
    HR_RECENT_DAYS = 3

    def _hr_candidates(self) -> list:
        """Fahrten ohne Gurt-Puls und ohne erfolgreichen HealthLog-Abruf (mit Uhrzeit)."""
        return self.db.query(
            "SELECT r.id, r.file_id, r.path, r.start_utc, r.duration_s, h.status, h.fetched_at FROM rides r "
            f"LEFT JOIN ride_hr h ON h.path = r.path WHERE r.start_utc IS NOT NULL AND r.path NOT LIKE '{MERGE_PREFIX}%' "
            "AND json_extract(r.analysis_json, '$.hr_source') IS NOT 'belt' AND (h.status IS NULL OR h.status != 'ok') "
            "ORDER BY r.start_utc DESC"
        )

    def hr_counts(self) -> dict:
        ok = self.db.one("SELECT COUNT(*) AS n FROM ride_hr WHERE status = 'ok'")["n"]
        return {"open": len(self._hr_candidates()), "ok": ok}

    def _hr_due(self, row, now: datetime) -> bool:
        if row["status"] is None:
            return True
        age = (now - datetime.fromisoformat(row["fetched_at"])).total_seconds()
        if row["status"] == "error":
            return age >= self.HR_RETRY_NONE_S
        start = datetime.fromisoformat(row["start_utc"])
        return (now - start).days < self.HR_RECENT_DAYS and age >= self.HR_RETRY_NONE_S  # none

    def healthlog_pending(self, limit: int = 20, now: datetime | None = None, fetch=None) -> int:
        """Holt den Puls für Fahrten ohne Gurt-Puls. Bei Verbindungsfehler Abbruch (später erneut); Rückgabe = bearbeitete Fahrten."""
        fetch = fetch or (lambda s, e: healthlog.fetch_heart_rate(self.db, s, e))
        now = now or datetime.now(UTC)
        done = 0
        for row in self._hr_candidates():
            if done >= limit:
                break
            if not self._hr_due(row, now):
                continue
            try:
                self._hr_fetch_row(row, fetch, now)
            except healthlog.HealthLogError as exc:
                log.warning("HealthLog-Puls nicht abrufbar (%s), später erneut", exc.code)
                return done
            done += 1
        return done

    def _hr_fetch_row(self, row, fetch, now: datetime) -> tuple[str, str | None]:
        """Holt und speichert den Puls einer Fahrt. Rückgabe (status, source); HealthLogError wird weitergereicht."""
        start = datetime.fromisoformat(row["start_utc"])
        try:
            res = fetch(start, start + timedelta(seconds=row["duration_s"]))
        except healthlog.HealthLogError:
            raise
        except Exception as exc:  # unerwartete Antwort: Fahrt vermerken
            log.exception("HealthLog-Puls für %s", row["path"])
            self._save_hr(row["path"], "error", detail=str(exc)[:200], now=now)
            return "error", None
        if res.points:
            self._save_hr(row["path"], "ok", res.source, res.workout_id, res.points, now=now)
            with self._lock:
                self._process_ride(row["file_id"], row["path"], self.read_raw(row["file_id"]))
            return "ok", res.source
        self._save_hr(row["path"], "none", now=now)
        return "none", None

    def healthlog_retry_all(self) -> int:
        """Vergisst alle Fehlschläge (none/error), damit sie erneut abgefragt werden; ok bleibt. Rückgabe = Anzahl."""
        n = self.db.one("SELECT COUNT(*) AS n FROM ride_hr WHERE status IN ('none', 'error')")["n"]
        self.db.execute("DELETE FROM ride_hr WHERE status IN ('none', 'error')")
        return n

    def healthlog_fetch_ride(self, ride_pk: int, fetch=None) -> dict | None:
        """Holt den Puls für eine Fahrt sofort (überschreibt einen vorhandenen). None = Fahrt unbekannt.
        Rückgabe {status: ok|none|error|belt|no_time, source}; HealthLogError wird weitergereicht."""
        row = self.db.one(
            "SELECT id, file_id, path, start_utc, duration_s, json_extract(analysis_json, '$.hr_source') AS hr_source "
            "FROM rides WHERE id = ?", (ride_pk,))
        if not row:
            return None
        if row["path"].startswith(MERGE_PREFIX):
            return {"status": "merged", "source": None}
        if row["hr_source"] == "belt":
            return {"status": "belt", "source": None}
        if not row["start_utc"]:
            return {"status": "no_time", "source": None}
        fetch = fetch or (lambda s, e: healthlog.fetch_heart_rate(self.db, s, e))
        status, source = self._hr_fetch_row(row, fetch, datetime.now(UTC))
        return {"status": status, "source": source}

    def _save_hr(self, path: str, status: str, source=None, workout_id=None, points=None, detail=None, now=None) -> None:
        self.db.execute(
            "INSERT INTO ride_hr(path, source, workout_id, points_json, status, detail, fetched_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET source = excluded.source, workout_id = excluded.workout_id, "
            "points_json = excluded.points_json, status = excluded.status, detail = excluded.detail, fetched_at = excluded.fetched_at",
            (path, source, workout_id, json.dumps([list(p) for p in points]) if points else None, status, detail,
             (now or datetime.now(UTC)).isoformat(timespec="seconds")),
        )

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
        row = self.db.one("SELECT file_id, path, ride_id, start_utc FROM rides WHERE id = ?", (ride_pk,))
        if not row:
            return None
        ride = self._ride_object(row)
        name = f"avinox-{row['start_utc'][:10] if row['start_utc'] else 'fahrt'}-{row['ride_id']}.gpx"
        return to_gpx(ride), name

    # ------------------------------------------------------------------ Fahrphysik

    def physics_config(self) -> dict:
        """Einstellungen der Fahrphysik: Systemgewicht (Pflicht), CdA-Vorgabe, Akkukapazität (optional)."""
        get = self.db.get_setting
        return {"mass_kg": get("physics_mass_kg"), "cda": get("physics_cda") or physics.DEFAULT_CDA,
                "cda_custom": get("physics_cda") is not None, "battery_wh": get("physics_battery_wh")}

    def set_physics_config(self, mass_kg, cda, battery_wh) -> None:
        """Speichert die Werte; ``None`` löscht (CdA fällt auf die Vorgabe, Akku auf unbekannt). Wirft ``ValueError``."""
        for name, value, lo, hi in (("mass_kg", mass_kg, 30, 300), ("cda", cda, 0.2, 1.5), ("battery_wh", battery_wh, 100, 3000)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi):
                raise ValueError(f"{name} muss zwischen {lo} und {hi} liegen")
        self.db.set_setting("physics_mass_kg", mass_kg)
        self.db.set_setting("physics_cda", cda)
        self.db.set_setting("physics_battery_wh", battery_wh)

    def _ride_object(self, row):
        """Geparste Fahrt zu einer rides-Zeile (``path``, ``file_id``); bei Zusammenführungen aus allen Teilen."""
        if row["path"].startswith(MERGE_PREFIX):
            return self._load_merged(row["path"])[0]
        return self._load_ride(row["path"], self.read_raw(row["file_id"]))[0]

    def _physics_for(self, row, cfg: dict) -> dict:
        """Fahrphysik einer Fahrt, aus ``ride_physics`` oder frisch berechnet (Schlüssel: Datei, Gewicht, CdA, Akku, Code-Version)."""
        stamp = self.db.one("SELECT created_at, member_paths_json FROM ride_merges WHERE group_path = ?", (row["path"],))
        ride_updated = self.db.one("SELECT updated_at FROM rides WHERE id = ?", (row["id"],))
        key = json.dumps([row["file_id"], (stamp["member_paths_json"], ride_updated["updated_at"]) if stamp else None, cfg["mass_kg"], cfg["cda"], cfg["battery_wh"], physics.PHYSICS_VERSION])
        cached = self.db.one("SELECT cache_key, result_json FROM ride_physics WHERE ride_id = ?", (row["id"],))
        if cached and cached["cache_key"] == key:
            return json.loads(cached["result_json"])
        ride = self._ride_object(row)
        result = physics.analyze_physics(ride.samples, cfg["mass_kg"], cfg["cda"], battery_wh=cfg["battery_wh"])
        self.db.execute(
            "INSERT INTO ride_physics(ride_id, cache_key, result_json, computed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(ride_id) DO UPDATE SET cache_key = excluded.cache_key, result_json = excluded.result_json, computed_at = excluded.computed_at",
            (row["id"], key, json.dumps(result, ensure_ascii=False, separators=(",", ":")), _now()),
        )
        return result

    def _physics_config_required(self) -> dict:
        cfg = self.physics_config()
        if not cfg["mass_kg"]:
            raise PhysicsSetupError("mass_missing")
        return cfg

    def ride_physics(self, ride_pk: int) -> dict | None:
        """Volle Fahrphysik einer Fahrt; ``None`` ohne Fahrt, ``PhysicsSetupError`` ohne Systemgewicht."""
        cfg = self._physics_config_required()
        with self._lock:
            row = self.db.one("SELECT id, path, file_id FROM rides WHERE id = ?", (ride_pk,))
            if not row:
                return None
            result = self._physics_for(row, cfg)
        names = self.mode_names()
        climbing = result.get("climbing")
        for item in (climbing or {}).get("assist", []):
            item["name"] = assist_name(item["level"], names)
        return {"ride_id": ride_pk, **result}

    def _day_bounds(self, start: str | None, end: str | None) -> tuple[str | None, str | None]:
        """Lokale Tage ``YYYY-MM-DD`` (einschließlich) als UTC-Grenzen; wirft ``ValueError``."""
        def day(value, name):
            try:
                return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=self.settings.tz)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} muss ein Datum YYYY-MM-DD sein") from exc
        lo = day(start, "start").astimezone(UTC).isoformat() if start else None
        hi = (day(end, "end") + timedelta(days=1)).astimezone(UTC).isoformat() if end else None
        return lo, hi

    def physics_compare(self, start: str | None = None, end: str | None = None, ride_ids: list[int] | None = None) -> dict:
        """Kernwerte der Fahrphysik je Fahrt (älteste zuerst) plus Median; Rollwiderstand > 1,25 × Median ist ``outlier``."""
        cfg = self._physics_config_required()
        lo, hi = self._day_bounds(start, end)
        where, args = ["r.start_utc IS NOT NULL"], []
        if lo:
            where.append("r.start_utc >= ?"); args.append(lo)
        if hi:
            where.append("r.start_utc < ?"); args.append(hi)
        if ride_ids is not None:
            where.append(f"r.id IN ({','.join('?' * len(ride_ids))})"); args.extend(ride_ids)
        rows = self.db.query(
            "SELECT r.id, r.path, r.file_id, r.ride_id, r.start_utc, m.custom_name, m.places_json FROM rides r LEFT JOIN ride_meta m ON m.path = r.path "
            f"WHERE {' AND '.join(where)} ORDER BY r.start_utc, r.path", tuple(args))
        items = [self._physics_item(row, cfg) for row in rows]
        return {"mass_kg": cfg["mass_kg"], "cda_default": cfg["cda"], "is_estimate": True, "count": len(items),
                "rides": physics.mark_outliers(items), "median": physics.median_of(items)}

    def _physics_item(self, row, cfg: dict) -> dict:
        with self._lock:
            result = self._physics_for(row, cfg)
        start = datetime.fromisoformat(row["start_utc"]).astimezone(self.settings.tz)
        item = physics.compare_item(result)
        return {"id": row["id"], "date": start.strftime("%Y-%m-%d"), "name": self._name_fields(row, None)["name"], **item}

    def ride_fit(self, ride_pk: int) -> tuple[bytes, str] | None:
        row = self.db.one("SELECT file_id, path, ride_id, start_utc FROM rides WHERE id = ?", (ride_pk,))
        if not row:
            return None
        ride = self._ride_object(row)
        name = f"avinox-{row['start_utc'][:10] if row['start_utc'] else 'fahrt'}-{row['ride_id']}.fit"
        return to_fit(ride), name

    def ride_raw(self, ride_pk: int) -> tuple[bytes, str] | None:
        row = self.db.one("SELECT file_id, path FROM rides WHERE id = ?", (ride_pk,))
        if not row or row["path"].startswith(MERGE_PREFIX):
            return None  # Zusammenführung hat keine eigene Rohdatei
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

    def dashboard(self, period: str = "week", offset: int = 0, today: date | None = None, lang: str = "de",
                  compare_mode: str = "elapsed") -> dict:
        """Übersicht für die Startseite (siehe ``dashboard.build``). ``today`` nur für Tests."""
        today = today or datetime.now(self.settings.tz).date()
        dash = dashboard.build(dashboard.load_rows(self.db, self.settings.tz), period, offset, today, lang, compare_mode)
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
            "SELECT id, start_utc AS time, duration_s, json_extract(analysis_json, '$.battery.motor_wh_per_pct') AS motor_wh_per_pct, "
            "json_extract(analysis_json, '$.battery.used_pct') AS used_pct FROM rides WHERE start_utc IS NOT NULL "
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
