"""Nutzungsstatistik ``buried_rec/buried_file_cur.json`` vom Avinox-Display.

Aufbau (siehe ERKENNTNISSE.md, Abschnitt 3a):

* JSON pro Zeile. ``{"general_msg": {...,"boot_times": n}}`` beginnt einen
  Einschaltvorgang (Session). Danach Ereignisse mit ``event_id`` und ``time_now``.
* Ein Ereignis ist oft auf mehrere Zeilen mit gleicher ``event_id`` und
  ``time_now`` verteilt; die Zeilen werden zusammengeführt.
* ``time_now`` ist UTC+8 ohne Tageswechsel (Stunde kann über 23 liegen).
  Vor dem ersten Uhrzeit-Abgleich steht das Jahr 1970.
* Fahrt-Ereignisse (``single_ride`` und alle mit gleicher Zeit) entstehen am
  Fahrtende; ``time_now`` = Ende der Fahrt laut ``ride_rec``-Header.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .ride_rec import Ride

_TIME_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})-(\d{1,2}):(\d{1,2}):(\d{1,2})$")
DEVICE_UTC_OFFSET_H = 8

RIDE_EVENTS = frozenset(
    {"single_ride", "rider_cadence", "rider_torque", "rider_heart_rate", "motor_torque", "ride_power", "boost_mode", "gear_shift", "assist_mode_usage"}
)
_RIDE_HEADER_KEYS = ("avg_speed", "distance", "duration")  # Wiederholung in jedem Fahrt-Ereignis


def parse_device_time(text: str) -> datetime | None:
    """``2026-11-5-22:58:43`` (UTC+8) -> UTC. None ohne gültige Uhrzeit (1970)."""
    m = _TIME_RE.match(text or "")
    if not m:
        return None
    year, month, day, hour, minute, second = map(int, m.groups())
    if year < 2000:
        return None
    base = datetime(year, month, day, tzinfo=UTC)
    return base + timedelta(hours=hour - DEVICE_UTC_OFFSET_H, minutes=minute, seconds=second)


def _value(text: object) -> object:
    if not isinstance(text, str):
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() and "." not in text else number


def _bands(values: dict, prefix: str) -> dict[str, float]:
    """``{"torque_less_50": 7361, "torque_50": 103}`` -> ``{"<50": 7361, "50": 103}``."""
    out = {}
    for key, val in values.items():
        if not key.startswith(prefix):
            continue
        band = key[len(prefix) :]
        band = band.replace("less_negative_", "<-").replace("less_", "<").replace("more_", ">").replace("negative_", "-")
        out[band] = val
    return out


@dataclass(slots=True)
class Event:
    event_id: str
    time_raw: str
    time: datetime | None
    values: dict[str, object]


@dataclass(slots=True)
class RideStats:
    """Statistik einer Fahrt aus der Nutzungsstatistik."""

    end_time: datetime | None
    duration_s: int
    distance_km: float
    avg_speed_kmh: float
    elev_gain_m: float
    elev_start_m: float
    battery_used_pct: int | None
    avg_cadence_rpm: float | None
    max_cadence_rpm: float | None
    avg_rider_torque_nm: float | None
    max_rider_torque_nm: float | None
    avg_heart_rate_bpm: float | None
    max_heart_rate_bpm: float | None
    seconds_per_gear: dict[int, int]
    seconds_per_assist_mode: dict[str, int]
    assist_mode_changes: int | None
    boost_s: int | None
    motor_torque_s: dict[str, int]
    """Sekunden je Motor-Drehmomentbereich (Nm, Schlüssel = Untergrenze)."""
    power_s: dict[str, int]
    """Sekunden je Leistungsbereich (W, vermutlich Motor), unter 500 W zusammengefasst."""
    session: Session | None = field(default=None, repr=False)


@dataclass(slots=True)
class Session:
    """Ein Einschaltvorgang des Displays."""

    boot_count: int | None
    events: list[Event] = field(default_factory=list)
    rides: list[RideStats] = field(default_factory=list)

    def latest(self, event_id: str) -> dict[str, object]:
        found = [e for e in self.events if e.event_id == event_id]
        return found[-1].values if found else {}

    @property
    def end_time(self) -> datetime | None:
        times = [e.time for e in self.events if e.time]
        return max(times) if times else None

    @property
    def firmware(self) -> dict[str, str]:
        v = self.latest("Firmware_version")
        return {k.lower(): str(val) for k, val in v.items()}

    @property
    def power_on_s(self) -> int | None:
        return self.latest("single_power").get("duration")

    @property
    def battery(self) -> dict[str, object]:
        """Akku-Werte der Session: Ladestand, Temperaturen, Ströme, Zellspannungen."""
        v = self.latest("battery_usage")
        if not v:
            return {}
        current = v.get("max_current")
        return {
            "start_pct": v.get("start_battery"),
            "end_pct": v.get("end_battery"),
            "start_temp_c": v.get("start_temperture"),
            "end_temp_c": v.get("end_temperture"),
            "max_temp_c": v.get("max_temperture"),
            "max_current_a": abs(current) / 1000 if isinstance(current, (int, float)) else None,
            "min_cell_mv": v.get("min_cell_voltage"),
            "max_cell_mv": v.get("max_cell_voltage"),
            "max_cell_delta_mv": v.get("max_delta_cell_voltage"),
            "min_total_v": v.get("min_total_voltage"),
        }

    @property
    def charge_cycles(self) -> int | None:
        return self.latest("battery_circle").get("number")

    @property
    def motor_electronics_temp_s(self) -> dict[str, object]:
        return _bands(self.latest("mos_ntc_temperture"), "temperature_")

    @property
    def battery_temp_s(self) -> dict[str, object]:
        return _bands(self.latest("battery_temperture"), "temperature_")

    @property
    def thermal_limit(self) -> bool:
        return bool(self.latest("mos_ntc_temperture").get("limit") or self.latest("battery_temperture").get("limit"))

    @property
    def pitch_km(self) -> dict[str, object]:
        """Strecke je Längsneigung (Grad, Schlüssel = Obergrenze)."""
        return _bands(self.latest("bike_pitch_attitude"), "slope_")

    @property
    def roll_km(self) -> dict[str, object]:
        """Seitenneigung je Bereich (Grad); Einheit vermutlich km wie bei ``pitch_km``."""
        return _bands(self.latest("bike_horizontal_attitude"), "roll_")

    @property
    def controller_presses(self) -> dict[str, object]:
        merged: dict[str, object] = {}
        for e in self.events:
            if e.event_id == "wireless_controller_usage":
                merged.update(e.values)
        return merged

    @property
    def assist_mode_settings(self) -> list[dict[str, object]]:
        """Einstellungen der Unterstützungsmodi beim Wechsel (``ass_mode_para``)."""
        return [dict(e.values, time=e.time.isoformat() if e.time else None) for e in self.events if e.event_id == "ass_mode_para" and e.values.get("mode")]


def _ride_stats(group: dict[str, dict[str, object]], time: datetime | None) -> RideStats:
    ride = group.get("single_ride", {})
    cad = group.get("rider_cadence", {})
    torque = group.get("rider_torque", {})
    hr = group.get("rider_heart_rate", {})
    gears = {int(k.rsplit("_", 1)[1]): v for k, v in group.get("gear_shift", {}).items() if k.startswith("gear_shift_")}
    usage = group.get("assist_mode_usage", {})
    modes = {k.removesuffix("_duration"): v for k, v in usage.items() if k.endswith("_duration")}
    boost = group.get("boost_mode", {})
    battery = ride.get("battery")
    return RideStats(
        end_time=time,
        duration_s=int(ride.get("duration", 0)),
        distance_km=float(ride.get("distance", 0.0)),
        avg_speed_kmh=float(ride.get("avg_speed", 0.0)),
        elev_gain_m=float(ride.get("elev_gain", 0.0)),
        elev_start_m=float(ride.get("elev_start", 0.0)),
        battery_used_pct=battery if isinstance(battery, int) else None,
        avg_cadence_rpm=cad.get("average"),
        max_cadence_rpm=cad.get("max"),
        avg_rider_torque_nm=torque.get("average"),
        max_rider_torque_nm=torque.get("max"),
        avg_heart_rate_bpm=hr.get("average") or None,
        max_heart_rate_bpm=hr.get("max") or None,
        seconds_per_gear=dict(sorted(gears.items())),
        seconds_per_assist_mode=modes,
        assist_mode_changes=usage.get("change_number"),
        boost_s=boost.get("boost_duration"),
        motor_torque_s=_bands(group.get("motor_torque", {}), "torque_"),
        power_s=_bands(group.get("ride_power", {}), "power_"),
    )


def parse_buried(text: str) -> list[Session]:
    sessions: list[Session] = []
    current: Session | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if "general_msg" in obj:
            current = Session(boot_count=(obj["general_msg"] or {}).get("boot_times"))
            sessions.append(current)
            continue
        if "event_id" not in obj:
            continue
        if current is None:
            current = Session(boot_count=None)
            sessions.append(current)
        event_id, time_raw = str(obj["event_id"]), str(obj.get("time_now", ""))
        values = {k: _value(v) for k, v in obj.items() if k not in ("event_id", "time_now")}
        last = current.events[-1] if current.events else None
        if last and last.event_id == event_id and last.time_raw == time_raw and not (set(values) & set(last.values) - set(_RIDE_HEADER_KEYS)):
            last.values.update(values)
        else:
            current.events.append(Event(event_id, time_raw, parse_device_time(time_raw), values))

    for session in sessions:
        groups: dict[str, dict[str, dict[str, object]]] = {}
        order: list[str] = []
        for e in session.events:
            if e.event_id in RIDE_EVENTS:
                if e.time_raw not in groups:
                    groups[e.time_raw] = {}
                    order.append(e.time_raw)
                groups[e.time_raw].setdefault(e.event_id, {}).update(e.values)
        for time_raw in order:
            if "single_ride" in groups[time_raw]:
                stats = _ride_stats(groups[time_raw], parse_device_time(time_raw))
                stats.session = session
                session.rides.append(stats)
    return sessions


def parse_buried_file(path: str | Path) -> list[Session]:
    return parse_buried(Path(path).read_text(encoding="utf-8", errors="replace"))


def match_ride(ride: Ride, sessions: list[Session]) -> RideStats | None:
    """Sucht die Statistik zu einer Fahrt: Endzeit, sonst Dauer + Distanz."""
    h = ride.header
    duration = h.sample_count or len(ride.samples)
    candidates = [r for s in sessions for r in s.rides]
    if h.has_wallclock:
        end = datetime.fromtimestamp(h.end, UTC)
        for r in candidates:
            if r.end_time and abs((r.end_time - end).total_seconds()) <= 5:
                return r
    best = None
    for r in candidates:
        if r.duration_s == duration and abs(r.distance_km * 1000 - h.distance_m) <= max(50, 0.1 * h.distance_m):
            if best is None or (r.end_time is None) == (not h.has_wallclock):
                best = r
    return best


def device_info(sessions: list[Session]) -> dict[str, object]:
    """Neuester Stand über alle Sessions: Firmware, Ladezyklen, Anzahl Einschaltvorgänge."""
    info: dict[str, object] = {"firmware": {}, "charge_cycles": None, "boot_count": None}
    for s in sessions:
        if s.firmware:
            info["firmware"] = s.firmware
        if s.charge_cycles is not None:
            info["charge_cycles"] = s.charge_cycles
        if s.boot_count is not None:
            info["boot_count"] = s.boot_count
    return info
