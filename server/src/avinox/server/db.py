"""SQLite-Speicher. Bewusst ohne ORM: wenige Tabellen, JSON-Spalten für die Auswertung."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL,          -- Pfad auf dem Display-Laufwerk, z. B. ride_rec/123/ride_rec_1008.proto
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- ride | buried | hms | index
    received_at TEXT NOT NULL,
    UNIQUE (path, size)
);

CREATE TABLE IF NOT EXISTS rides (
    id               INTEGER PRIMARY KEY,
    path             TEXT NOT NULL UNIQUE,   -- eindeutig, rideIds wiederholen sich zwischen Ordnern
    file_id          INTEGER NOT NULL REFERENCES files(id),
    ride_id          INTEGER NOT NULL,
    start_utc        TEXT,                   -- NULL: Display hatte noch keine Uhrzeit
    duration_s       INTEGER NOT NULL,
    moving_time_s    INTEGER NOT NULL,
    distance_m       INTEGER NOT NULL,
    ascent_m         REAL,
    descent_m        REAL,
    avg_speed_kmh    REAL,
    max_speed_kmh    REAL,
    avg_rider_power_w REAL,
    avg_motor_power_w REAL,
    avg_cadence_rpm  REAL,
    calories_kcal    REAL,
    battery_start_pct INTEGER,
    battery_end_pct  INTEGER,
    has_gps          INTEGER NOT NULL,
    has_device_stats INTEGER NOT NULL,
    warnings         INTEGER NOT NULL,
    parser_version   INTEGER NOT NULL,
    analysis_json    TEXT NOT NULL,
    geojson          TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rides_start ON rides(start_utc);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_events (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    client_ip TEXT,
    device_id TEXT,
    kind      TEXT NOT NULL,   -- manifest | upload_chunk_error | file_new | file_known | auth_error | report | heartbeat
    path      TEXT,
    size      INTEGER,
    bytes     INTEGER,
    status    INTEGER,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS sync_events_ts ON sync_events(ts);

CREATE TABLE IF NOT EXISTS devices (
    device_id        TEXT PRIMARY KEY,
    last_seen        TEXT,
    last_ip          TEXT,
    firmware         TEXT,
    last_report_json TEXT,
    last_sync_json   TEXT,
    last_sync_at     TEXT
);

CREATE TABLE IF NOT EXISTS firmware (
    id          INTEGER PRIMARY KEY,
    version     TEXT NOT NULL,
    project     TEXT NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    is_current  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project, version)
);

-- Gelöschte Fahrten: Pfad = rides.path; gesperrt für jede Größe dieses Pfads (kein Wiederauftauchen bei Sync/Neuberechnung)
CREATE TABLE IF NOT EXISTS deleted_rides (
    path        TEXT PRIMARY KEY,
    deleted_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ride_meta (
    path        TEXT PRIMARY KEY,   -- = rides.path (bleibt bei Neuberechnung stabil)
    custom_name TEXT,               -- von Hand gesetzt, NULL = automatisch
    places_json TEXT,               -- {"start": "...", "end": "...", "far": "..."} (Werte können null sein)
    geocoded_at TEXT                -- gesetzt, sobald erfolgreich (auch wenn keine Orte gefunden)
);

CREATE TABLE IF NOT EXISTS geocache (
    key        TEXT PRIMARY KEY,    -- "lat,lon" auf 3 Nachkommastellen gerundet
    name       TEXT,                -- NULL = Dienst kannte nichts
    fetched_at TEXT NOT NULL
);
"""


class Database:
    """Dünne Hülle um sqlite3; eine Verbindung, durch ein Lock geschützt."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def get_setting(self, key: str, default=None):
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
