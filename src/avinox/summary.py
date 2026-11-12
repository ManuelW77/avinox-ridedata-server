"""Kennzahlen einer Fahrt aus den Samples."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .ride_rec import Ride, Sample

MOVING_MIN_KMH = 1.0
"""Ab dieser Geschwindigkeit zählt ein Sample als Fahrzeit."""
MAX_GAP_S = 5
"""Längere Zeitsprünge zwischen Samples sind Pausen (Aufzeichnung angehalten)."""
CLIMB_THRESHOLD_M = 3.0
"""Hysterese für Höhenmeter: kleinere Schwankungen der Barometerhöhe zählen nicht."""


@dataclass(slots=True)
class Summary:
    ride_id: int
    start_time: datetime | None
    """UTC; None, wenn das Display beim Fahren noch keine Uhrzeit hatte."""
    samples: int
    duration_s: int
    moving_time_s: int
    distance_km: float
    ascent_m: float | None
    descent_m: float | None
    avg_speed_kmh: float | None
    """Durchschnitt über die Fahrzeit."""
    max_speed_kmh: float
    avg_rider_power_w: float | None
    """Über die Fahrzeit, Nullen beim Rollen eingeschlossen."""
    max_rider_power_w: float
    avg_motor_power_w: float | None
    max_motor_power_w: float
    avg_cadence_rpm: float | None
    """Nur Samples mit Trittfrequenz > 0."""
    max_cadence_rpm: float
    avg_heart_rate_bpm: float | None
    max_heart_rate_bpm: int | None
    rider_energy_kj: float
    motor_energy_wh: float
    battery_start_pct: int | None
    battery_end_pct: int | None
    temperature_avg_c: float | None
    gps_points: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start_time"] = self.start_time.isoformat() if self.start_time else None
        return d


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _intervals(samples: list[Sample]) -> list[int]:
    """Dauer, die jedes Sample abdeckt (Sekunden). Pausen und Zeitsprünge zählen 0."""
    out = []
    for cur, nxt in zip(samples, samples[1:]):
        dt = (nxt.timestamp - cur.timestamp) if cur.timestamp is not None and nxt.timestamp is not None else 1
        out.append(dt if 0 <= dt <= MAX_GAP_S else 0)
    if samples:
        out.append(1)
    return out


def climb(altitudes: list[float], threshold: float = CLIMB_THRESHOLD_M) -> tuple[float, float]:
    """Anstieg und Abstieg mit Hysterese.

    Zählt von Wendepunkt zu Wendepunkt. Ein Wendepunkt gilt erst, wenn die Höhe
    danach um mindestens ``threshold`` in die Gegenrichtung läuft; kleineres
    Rauschen der Barometerhöhe fällt so heraus, Gipfel und Täler bleiben voll.
    """
    if not altitudes:
        return 0.0, 0.0
    up = down = 0.0
    anchor = extreme = altitudes[0]
    trend = 0
    for alt in altitudes[1:]:
        if trend == 0:
            if abs(alt - anchor) >= threshold:
                trend = 1 if alt > anchor else -1
                extreme = alt
        elif trend * (alt - extreme) > 0:
            extreme = alt
        elif abs(extreme - alt) >= threshold:
            if trend > 0:
                up += extreme - anchor
            else:
                down += anchor - extreme
            anchor, extreme, trend = extreme, alt, -trend
    if trend > 0:
        up += extreme - anchor
    elif trend < 0:
        down += anchor - extreme
    return up, down


def summarize(ride: Ride) -> Summary:
    s = ride.samples
    dts = _intervals(s)
    moving = [(x, dt) for x, dt in zip(s, dts) if x.speed_kmh >= MOVING_MIN_KMH and dt > 0]
    moving_time = sum(dt for _, dt in moving)

    def moving_avg(attr: str) -> float | None:
        if not moving_time:
            return None
        return sum(getattr(x, attr) * dt for x, dt in moving) / moving_time

    altitudes = [x.altitude_m for x in s if x.altitude_m is not None]
    up, down = climb(altitudes) if altitudes else (None, None)
    hr = [x.heart_rate_bpm for x in s if x.heart_rate_bpm]
    battery = [x.battery_pct for x in s if x.battery_pct is not None]
    temps = [x.temperature_c for x in s if x.temperature_c is not None]
    distances = [x.distance_m for x in s]

    header = ride.header
    start = header.start_time
    if start is None and s and s[0].timestamp and s[0].timestamp >= 1_000_000_000:
        start = datetime.fromtimestamp(s[0].timestamp, UTC)

    return Summary(
        ride_id=header.ride_id,
        start_time=start,
        samples=len(s),
        duration_s=sum(dts),
        moving_time_s=moving_time,
        distance_km=round(max(distances, default=0) / 1000, 3),
        ascent_m=up,
        descent_m=down,
        avg_speed_kmh=moving_avg("speed_kmh"),
        max_speed_kmh=max((x.speed_kmh for x in s), default=0.0),
        avg_rider_power_w=moving_avg("rider_power_w"),
        max_rider_power_w=max((x.rider_power_w for x in s), default=0.0),
        avg_motor_power_w=moving_avg("motor_power_w"),
        max_motor_power_w=max((x.motor_power_w for x in s), default=0.0),
        avg_cadence_rpm=_mean([x.cadence_rpm for x in s if x.cadence_rpm > 0]),
        max_cadence_rpm=max((x.cadence_rpm for x in s), default=0.0),
        avg_heart_rate_bpm=_mean(hr),
        max_heart_rate_bpm=max(hr, default=None),
        rider_energy_kj=sum(x.rider_power_w * dt for x, dt in zip(s, dts)) / 1000,
        motor_energy_wh=sum(x.motor_power_w * dt for x, dt in zip(s, dts)) / 3600,
        battery_start_pct=battery[0] if battery else None,
        battery_end_pct=battery[-1] if battery else None,
        temperature_avg_c=_mean(temps),
        gps_points=sum(1 for x in s if x.latitude is not None),
    )
