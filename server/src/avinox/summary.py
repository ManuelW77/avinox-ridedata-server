"""Kennzahlen einer Fahrt.

Hauptwerte kommen aus dem Header: Das Display rechnet sie selbst, und die
Avinox-App zeigt genau diese Zahlen. Aus den Samples kommen nur Werte, die der
Header nicht hat (Fahrzeit, Puls, Akku, Energie, Temperatur) sowie eigene
Vergleichswerte (``samples_*``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .ride_rec import Ride, Sample

MOVING_MIN_KMH = 1.0
"""Ab dieser Geschwindigkeit zählt ein Sample als Fahrzeit."""
MAX_GAP_S = 5
"""Längere Zeitsprünge zwischen Samples sind Pausen (Aufzeichnung angehalten)."""
CLIMB_THRESHOLD_M = 3.0
"""Hysterese für die eigenen Höhenmeter: kleinere Schwankungen der Barometerhöhe zählen nicht."""


@dataclass(slots=True)
class Summary:
    ride_id: int
    start_time: datetime | None
    """UTC; None, wenn das Display beim Fahren noch keine Uhrzeit hatte."""
    duration_s: int
    """Aufzeichnungsdauer wie in der App (= Anzahl Samples à 1 s)."""
    elapsed_s: int
    """Ende minus Start laut Header, inklusive Lücken."""
    moving_time_s: int
    """Eigene Berechnung: Samples mit mindestens ``MOVING_MIN_KMH``."""
    distance_km: float

    # --- wie in der App (Header) ---
    ascent_m: float
    descent_m: float
    avg_gradient_pct: float
    max_gradient_pct: float
    avg_speed_kmh: float
    max_speed_kmh: float
    avg_rider_power_w: float | None
    """Zeitgewichtet über Samples in Bewegung, Rollen (0 W) zählt mit; None ohne Bewegungszeit."""
    avg_pedaling_power_w: float | None
    """Zeitgewichtet über Samples in Bewegung mit Fahrerleistung > 0."""
    header_avg_rider_power_w: float
    """Ø-Fahrerleistung laut Display-Header (zählt Standzeit mit), nur zum Vergleich."""
    max_rider_power_w: float
    avg_rider_torque_nm: float
    max_rider_torque_nm: float
    avg_motor_power_w: float | None
    """Zeitgewichtet über Samples in Bewegung, Rollen (0 W) zählt mit; None ohne Bewegungszeit."""
    avg_motor_assist_power_w: float | None
    """Zeitgewichtet über Samples in Bewegung mit Motorleistung > 0."""
    header_avg_motor_power_w: float
    """Ø-Motorleistung laut Display-Header, nur zum Vergleich."""
    max_motor_power_w: float
    avg_motor_torque_nm: float
    max_motor_torque_nm: float
    avg_total_power_w: float
    max_total_power_w: float
    avg_total_torque_nm: float
    max_total_torque_nm: float
    avg_cadence_rpm: float
    max_cadence_rpm: float
    calories_kcal: float

    # --- aus den Samples ---
    avg_heart_rate_bpm: float | None
    max_heart_rate_bpm: int | None
    battery_start_pct: int | None
    battery_end_pct: int | None
    temperature_avg_c: float | None
    rider_energy_kj: float
    """Integral der Fahrerleistung."""
    motor_energy_wh: float
    """Integral der Motorleistung."""
    gps_points: int
    samples_avg_moving_speed_kmh: float | None
    samples_ascent_m: float | None
    """Eigene Höhenmeter aus den Höhen-Samples, liegen meist deutlich über ``ascent_m``."""
    samples_descent_m: float | None

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
    header = ride.header
    st = header.stats
    dts = _intervals(s)
    moving = [(x, dt) for x, dt in zip(s, dts) if x.speed_kmh >= MOVING_MIN_KMH and dt > 0]
    moving_time = sum(dt for _, dt in moving)
    pedaling = [(x, dt) for x, dt in moving if x.rider_power_w > 0]
    pedaling_time = sum(dt for _, dt in pedaling)
    assisting = [(x, dt) for x, dt in moving if x.motor_power_w > 0]
    assist_time = sum(dt for _, dt in assisting)
    avg_motor = round(sum(x.motor_power_w * dt for x, dt in moving) / moving_time) if moving_time else None
    avg_assist = round(sum(x.motor_power_w * dt for x, dt in assisting) / assist_time) if assist_time else None
    avg_rider = round(sum(x.rider_power_w * dt for x, dt in moving) / moving_time) if moving_time else None
    avg_pedaling = round(sum(x.rider_power_w * dt for x, dt in pedaling) / pedaling_time) if pedaling_time else None

    altitudes = [x.altitude_m for x in s if x.altitude_m is not None]
    up, down = climb(altitudes) if altitudes else (None, None)
    hr = [x.heart_rate_bpm for x in s if x.heart_rate_bpm]
    battery = [x.battery_pct for x in s if x.battery_pct is not None]
    temps = [x.temperature_c for x in s if x.temperature_c is not None]

    start = header.start_time
    if start is None and s and s[0].timestamp and s[0].timestamp >= 1_000_000_000:
        start = datetime.fromtimestamp(s[0].timestamp, UTC)

    return Summary(
        ride_id=header.ride_id,
        start_time=start,
        duration_s=header.sample_count or len(s),
        elapsed_s=header.duration_s,
        moving_time_s=moving_time,
        distance_km=round(header.distance_m / 1000, 3),
        ascent_m=header.ascent_m,
        descent_m=header.descent_m,
        avg_gradient_pct=st.avg_gradient_pct,
        max_gradient_pct=st.max_gradient_pct,
        avg_speed_kmh=st.avg_speed_kmh,
        max_speed_kmh=st.max_speed_kmh,
        avg_rider_power_w=avg_rider,
        avg_pedaling_power_w=avg_pedaling,
        header_avg_rider_power_w=st.avg_rider_power_w,
        max_rider_power_w=st.max_rider_power_w,
        avg_rider_torque_nm=st.avg_rider_torque_nm,
        max_rider_torque_nm=st.max_rider_torque_nm,
        avg_motor_power_w=avg_motor,
        avg_motor_assist_power_w=avg_assist,
        header_avg_motor_power_w=st.avg_motor_power_w,
        max_motor_power_w=st.max_motor_power_w,
        avg_motor_torque_nm=st.avg_motor_torque_nm,
        max_motor_torque_nm=st.max_motor_torque_nm,
        avg_total_power_w=st.avg_total_power_w,
        max_total_power_w=st.max_total_power_w,
        avg_total_torque_nm=st.avg_total_torque_nm,
        max_total_torque_nm=st.max_total_torque_nm,
        avg_cadence_rpm=st.avg_cadence_rpm,
        max_cadence_rpm=st.max_cadence_rpm,
        calories_kcal=st.calories_kcal,
        avg_heart_rate_bpm=_mean(hr),
        max_heart_rate_bpm=max(hr, default=None),
        battery_start_pct=battery[0] if battery else None,
        battery_end_pct=battery[-1] if battery else None,
        temperature_avg_c=_mean(temps),
        rider_energy_kj=sum(x.rider_power_w * dt for x, dt in zip(s, dts)) / 1000,
        motor_energy_wh=sum(x.motor_power_w * dt for x, dt in zip(s, dts)) / 3600,
        gps_points=sum(1 for x in s if x.latitude is not None),
        samples_avg_moving_speed_kmh=(sum(x.speed_kmh * dt for x, dt in moving) / moving_time) if moving_time else None,
        samples_ascent_m=None if up is None else round(up, 1),
        samples_descent_m=None if down is None else round(down, 1),
    )
