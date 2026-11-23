"""Fahrphysik: Roll- und Luftwiderstand, Energiebilanz, Bergauf-Werte und Bedingungen einer Fahrt.

Reine Funktionen ohne Datenbank. Aus Leistung, Tempo, Steigung und Beschleunigung wird je Sekunde
der Widerstand der Fahrt bestimmt:

    P_gesamt · η / v − m·g·sin α − m·a  =  m·g·cos α · Crr  +  ½ · ρ · v² · CdA

Crr (Rollwiderstand) und CdA (Luftwiderstandsfläche) sind **Schätzwerte**: Es gibt keinen Windmesser,
die Leistungswerte stammen aus dem Reverse Engineering, und die Höhe kommt vom Barometer. Wind wird
nur über Hin- und Rückweg derselben Strecke herausgemittelt (``conditions.out_and_back``).
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from .analysis import assist_name
from .ride_rec import Sample

PHYSICS_VERSION = 1
"""Hochzählen, wenn sich die Berechnung ändert: entwertet die zwischengespeicherten Ergebnisse."""

G = 9.81
RHO_DEFAULT = 1.225
DEFAULT_CDA = 0.7
DEFAULT_ETA = 0.96

MAX_GAP_S = 5
GRADE_WINDOW_M = 75.0
"""Halbe Fensterbreite für die Steigung (± 75 m = 150 m)."""
GRADE_MIN_SPAN_M = 100.0
ACCEL_WINDOW_S = 3.0

MIN_SPEED_KMH = 12.0
MAX_ACCEL = 0.15
MIN_POWER_W = 30.0
MAX_GRADE = 0.04
MIN_SECONDS = 120
FIT_MIN_SECONDS = 300
FIT_MIN_SPEED_STD = 1.5
CRR_RANGE = (0.003, 0.05)
CDA_RANGE = (0.2, 1.5)
BOOT_BLOCK_S = 60
BOOT_REPS = 50
BOOT_SEED = 20261123
FLAT_SPEEDS_KMH = (20, 25)

CLIMB_MIN_GRADE = 0.04
CLIMB_MIN_S = 60
CLIMB_MIN_KMH = 5.0
"""Schieben und Stehen zählen nicht als Bergauffahrt."""
ASSIST_MIN_RIDER_W = 30.0
BATTERY_MIN_USED_PCT = 10

CELL_M = 200.0
LOOP_COVERAGE = 0.6
LOOP_MIN_CELLS = 8
LOOP_DIRECTION = 0.7

P_RANGE_KPA = (60.0, 110.0)
T_RANGE_C = (-30.0, 50.0)
DEVICE_TEMP_DRIFT_C = 4.0
"""Fällt/steigt die Temperatur im Lauf der Fahrt um so viel, hängt der Sensor am Gerät und folgt der Luft nur träge."""


@dataclass(slots=True)
class _Fail:
    """Block ohne Datengrundlage: wird in der Ausgabe zu ``null`` plus ``<block>_reason``."""

    reason: str


# ------------------------------------------------------------------ Vorverarbeitung


@dataclass(slots=True)
class _Prep:
    s: list[Sample]
    t: list[float]
    w: list[int]
    """Dauer je Sample in Sekunden; 0 vor Lücken."""
    sec: list[tuple[int, int]]
    """Abschnitte als (Anfang, Ende) ohne Lücke, Ende exklusiv."""
    v: list[float]
    dist: list[float]
    slope: list[float | None] = field(default_factory=list)
    acc: list[float | None] = field(default_factory=list)


def _times(samples: list[Sample]) -> tuple[list[float], list[int]]:
    t, w = [0.0], []
    for i, (cur, nxt) in enumerate(zip(samples, samples[1:])):
        if cur.timestamp is not None and nxt.timestamp is not None:
            dt = nxt.timestamp - cur.timestamp
        else:
            dt = 1
        w.append(dt if 0 < dt <= MAX_GAP_S else 0)
        t.append(t[-1] + (dt if dt > 0 else 1))
    if samples:
        w.append(1)
    return t, w


def _sections(w: list[int], t: list[float], n: int) -> list[tuple[int, int]]:
    out, start = [], 0
    for i in range(n):
        if w[i] == 0 and i < n - 1:  # Lücke oder doppelter Zeitstempel hinter i
            out.append((start, i + 1))
            start = i + 1
    if n:
        out.append((start, n))
    return out


def _window_slope(p: _Prep, i: int, a: int, b: int) -> float | None:
    lo = bisect_left(p.dist, p.dist[i] - GRADE_WINDOW_M, a, b)
    hi = bisect_right(p.dist, p.dist[i] + GRADE_WINDOW_M, a, b) - 1
    span = p.dist[hi] - p.dist[lo]
    ha, hb = p.s[hi].altitude_m, p.s[lo].altitude_m
    if span < GRADE_MIN_SPAN_M or ha is None or hb is None:
        return None
    return (ha - hb) / span


def _window_acc(p: _Prep, i: int, a: int, b: int) -> float | None:
    lo = bisect_left(p.t, p.t[i] - ACCEL_WINDOW_S, a, b)
    hi = bisect_right(p.t, p.t[i] + ACCEL_WINDOW_S, a, b) - 1
    if p.t[hi] - p.t[lo] < ACCEL_WINDOW_S:
        return None
    return (p.v[hi] - p.v[lo]) / (p.t[hi] - p.t[lo])


def prepare(samples: list[Sample]) -> _Prep:
    """Zeit, Abschnitte, Steigung (150-m-Fenster) und Beschleunigung (± 3 s) je Sample."""
    t, w = _times(samples)
    p = _Prep(
        s=samples, t=t, w=w, sec=_sections(w, t, len(samples)),
        v=[x.speed_kmh / 3.6 for x in samples], dist=[float(x.distance_m) for x in samples],
        slope=[None] * len(samples), acc=[None] * len(samples),
    )
    for a, b in p.sec:
        for i in range(a, b):
            p.slope[i] = _window_slope(p, i, a, b)
            p.acc[i] = _window_acc(p, i, a, b)
    return p


def _air_density(samples: list[Sample]) -> tuple[float, str, float | None]:
    """(ρ, Quelle, mittlere Temperatur). Quelle ``measured`` oder ``default``."""
    temps = [x.temperature_c for x in samples if x.temperature_c is not None]
    press = [x.pressure_kpa for x in samples if x.pressure_kpa is not None]
    avg_t = sum(temps) / len(temps) if temps else None
    if temps and press:
        p_kpa = sum(press) / len(press)
        if P_RANGE_KPA[0] <= p_kpa <= P_RANGE_KPA[1] and T_RANGE_C[0] <= avg_t <= T_RANGE_C[1]:
            return p_kpa * 1000 / (287.05 * (avg_t + 273.15)), "measured", avg_t
    return RHO_DEFAULT, "default", avg_t


def _temp_is_device(samples: list[Sample]) -> bool:
    """Sensor folgt der Luft nur träge (warmer Start, dann Abkühlen), also vermutlich Gerätetemperatur."""
    temps = [x.temperature_c for x in samples if x.temperature_c is not None]
    if len(temps) < 300:
        return False
    head, tail = sum(temps[:60]) / 60, sum(temps[-60:]) / 60
    return abs(head - tail) >= DEVICE_TEMP_DRIFT_C


# ------------------------------------------------------------------ Kleinste Quadrate


_ZERO = (0.0,) * 6


def _add(a: tuple, b: tuple) -> tuple:
    return tuple(x + y for x, y in zip(a, b))


def _row(w: float, x1: float, x2: float, y: float) -> tuple:
    """Summenglieder (S11, S12, S22, S1y, S2y, Sw) einer Beobachtung mit Gewicht ``w``."""
    return (w * x1 * x1, w * x1 * x2, w * x2 * x2, w * x1 * y, w * x2 * y, w)


def _fit2(s: tuple) -> tuple[float, float] | None:
    s11, s12, s22, s1y, s2y, _ = s
    det = s11 * s22 - s12 * s12
    if s11 <= 0 or s22 <= 0 or det <= 1e-9 * s11 * s22:
        return None
    return (s1y * s22 - s2y * s12) / det, (s11 * s2y - s12 * s1y) / det


def _fit1(s: tuple, cda: float) -> float | None:
    return (s[3] - cda * s[1]) / s[0] if s[0] > 0 else None


def _plausible(crr: float, cda: float) -> bool:
    return CRR_RANGE[0] <= crr <= CRR_RANGE[1] and CDA_RANGE[0] <= cda <= CDA_RANGE[1]


def _observations(p: _Prep, mass: float, eta: float, rho: float) -> list[tuple]:
    """Gültige Sekunden: (Index, Zeit, Gewicht, x1, x2, y, v)."""
    out = []
    for i, x in enumerate(p.s):
        slope, acc, v = p.slope[i], p.acc[i], p.v[i]
        if (p.w[i] == 0 or slope is None or acc is None or x.speed_kmh < MIN_SPEED_KMH or abs(acc) >= MAX_ACCEL
                or x.total_power_w <= MIN_POWER_W or abs(slope) > MAX_GRADE):
            continue
        alpha = math.atan(slope)
        y = x.total_power_w * eta / v - mass * G * math.sin(alpha) - mass * acc
        out.append((i, p.t[i], p.w[i], mass * G * math.cos(alpha), 0.5 * rho * v * v, y, v))
    return out


def _sums(obs: list[tuple]) -> tuple:
    total = _ZERO
    for _, _, w, x1, x2, y, _ in obs:
        total = _add(total, _row(w, x1, x2, y))
    return total


def _speed_std(obs: list[tuple]) -> float:
    sw = sum(o[2] for o in obs)
    if not sw:
        return 0.0
    mean = sum(o[2] * o[6] for o in obs) / sw
    return math.sqrt(sum(o[2] * (o[6] - mean) ** 2 for o in obs) / sw)


def _solve(obs: list[tuple], cda_default: float) -> tuple[float, float, str] | None:
    """(Crr, CdA, Quelle). Zwei Parameter nur bei genug Tempo-Spanne und plausiblem Ergebnis."""
    s = _sums(obs)
    if s[5] >= FIT_MIN_SECONDS and _speed_std(obs) >= FIT_MIN_SPEED_STD:
        fit = _fit2(s)
        if fit and _plausible(*fit):
            return fit[0], fit[1], "fitted"
    crr = _fit1(s, cda_default)
    return None if crr is None else (crr, cda_default, "assumed")


def _percentile(sorted_vals: list[float], q: float) -> float:
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _bootstrap(obs: list[tuple], source: str, cda: float) -> tuple[float, float] | None:
    """10. und 90. Perzentil von Crr, Wiederholung über 60-s-Blöcke (fester Seed)."""
    blocks: dict[int, tuple] = {}
    for o in obs:
        key = int(o[1] // BOOT_BLOCK_S)
        blocks[key] = _add(blocks.get(key, _ZERO), _row(o[2], o[3], o[4], o[5]))
    parts = list(blocks.values())
    if len(parts) < 2:
        return None
    rng = random.Random(BOOT_SEED)
    crrs = []
    for _ in range(BOOT_REPS):
        total = _ZERO
        for _ in parts:
            total = _add(total, rng.choice(parts))
        if source == "fitted":
            fit = _fit2(total)
            crr = fit[0] if fit else None
        else:
            crr = _fit1(total, cda)
        if crr is not None:
            crrs.append(crr)
    if len(crrs) < BOOT_REPS // 2:
        return None
    crrs.sort()
    return _percentile(crrs, 0.1), _percentile(crrs, 0.9)


def _flat_power(crr: float, cda: float, rho: float, mass: float, eta: float) -> dict:
    """Bedarf auf der Ebene bei Windstille am Antrieb (inkl. η), getrennt nach Rollen und Luft."""
    out = {}
    for kmh in FLAT_SPEEDS_KMH:
        v = kmh / 3.6
        roll, air = crr * mass * G * v / eta, 0.5 * rho * cda * v ** 3 / eta
        out[str(kmh)] = {"w": round(roll + air), "roll_w": round(roll), "air_w": round(air)}
    return out


def _resistance(p: _Prep, mass: float, cda: float, eta: float, rho: float, rho_source: str,
                avg_temp: float | None) -> tuple[dict | _Fail, list[tuple], tuple | None]:
    obs = _observations(p, mass, eta, rho)
    seconds = sum(o[2] for o in obs)
    if seconds < MIN_SECONDS:
        return _Fail("too_little_valid_data"), obs, None
    solved = _solve(obs, cda)
    if solved is None:
        return _Fail("not_solvable"), obs, None
    crr, cda_used, source = solved
    band = _bootstrap(obs, source, cda_used)
    block = {
        "crr": round(crr, 4),
        "crr_p10": round(band[0], 4) if band else None,
        "crr_p90": round(band[1], 4) if band else None,
        "cda": round(cda_used, 3), "cda_source": source, "seconds_used": seconds,
        "rho": round(rho, 3), "rho_source": rho_source,
        "avg_temp_c": round(avg_temp, 1) if avg_temp is not None else None,
        "flat_power_w": _flat_power(crr, cda_used, rho, mass, eta),
        "estimated_fields": ["crr", "crr_p10", "crr_p90", "cda", "flat_power_w"],
    }
    return block, obs, (crr, cda_used, source)


# ------------------------------------------------------------------ Energie


def _required_wh(p: _Prep, mass: float, eta: float, rho: float, crr: float, cda: float) -> tuple[float, float]:
    """(nötige mechanische Energie, gemessene mechanische Energie) in Wh über dieselben Sekunden.

    Nötig = Roll + Luft + Hub + kinetisch je Sekunde, nur positive Anteile (bergab liefert niemand Energie ab).
    """
    need = got = 0.0
    for i, x in enumerate(p.s):
        slope, acc, v = p.slope[i], p.acc[i], p.v[i]
        if p.w[i] == 0 or slope is None or acc is None or v <= 0:
            continue
        alpha = math.atan(slope)
        force = crr * mass * G * math.cos(alpha) + 0.5 * rho * cda * v * v + mass * G * math.sin(alpha) + mass * acc
        need += max(0.0, force * v) * p.w[i] / 3600
        got += x.total_power_w * eta * p.w[i] / 3600
    return need, got


def _battery(samples: list[Sample], motor_wh: float, battery_wh: float | None) -> dict | _Fail:
    pcts = [x.battery_pct for x in samples if x.battery_pct is not None]
    if not pcts:
        return _Fail("no_battery_data")
    used = pcts[0] - pcts[-1]
    if used < BATTERY_MIN_USED_PCT:
        return _Fail("battery_used_too_small")
    if not battery_wh:
        return _Fail("battery_capacity_missing")
    drawn = used / 100 * battery_wh
    return {"used_pct": used, "battery_wh": round(drawn, 1), "motor_wh": round(motor_wh, 1),
            "motor_battery_efficiency_pct": round(100 * motor_wh / drawn, 1), "estimated_fields": ["motor_battery_efficiency_pct"]}


def _energy(p: _Prep, mass: float, eta: float, rho: float, res: tuple | None, battery_wh: float | None) -> dict | _Fail:
    s = p.s
    km = (max(p.dist) - min(p.dist)) / 1000 if s else 0.0
    if km < 0.5:
        return _Fail("too_short")
    rider_wh = sum(x.rider_power_w * w for x, w in zip(s, p.w)) / 3600
    motor_wh = sum(x.motor_power_w * w for x, w in zip(s, p.w)) / 3600
    total = rider_wh + motor_wh
    out = {
        "wh_per_km": round(total / km, 1), "rider_wh_per_km": round(rider_wh / km, 1), "motor_wh_per_km": round(motor_wh / km, 1),
        "motor_share_pct": round(100 * motor_wh / total, 1) if total else None,
        "physics_ratio": None, "physics_reason": None, "battery": None, "battery_reason": None,
        "estimated_fields": ["physics_ratio"],
    }
    battery = _battery(s, motor_wh, battery_wh)
    out["battery" if isinstance(battery, dict) else "battery_reason"] = battery if isinstance(battery, dict) else battery.reason
    if res is None:
        out["physics_reason"] = "no_resistance_estimate"
        return out
    need, got = _required_wh(p, mass, eta, rho, res[0], res[1])
    if need > 0:
        out["physics_ratio"] = round(got / need, 2)
        out["physics_required_wh"] = round(need, 1)
        out["measured_mechanical_wh"] = round(got, 1)
    else:
        out["physics_reason"] = "no_required_energy"
    return out


# ------------------------------------------------------------------ Bergauf


def _climb_runs(p: _Prep) -> list[tuple[int, int]]:
    """Läufe mit Steigung ≥ 4 % und mindestens 60 s am Stück (Ende exklusiv)."""
    runs = []
    for a, b in p.sec:
        start = None
        for i in range(a, b + 1):
            hit = i < b and p.slope[i] is not None and p.slope[i] >= CLIMB_MIN_GRADE and p.s[i].speed_kmh >= CLIMB_MIN_KMH
            if hit and start is None:
                start = i
            elif not hit and start is not None:
                if sum(p.w[start:i]) >= CLIMB_MIN_S:
                    runs.append((start, i))
                start = None
    return runs


def assist_factors(samples: list[Sample], weights: list[int]) -> list[dict]:
    """Echter Unterstützungsfaktor je Stufe: Motorenergie / Fahrerenergie, nur Sekunden mit Fahrerleistung > 30 W."""
    groups: dict[int, dict[str, float]] = {}
    for x, w in zip(samples, weights):
        if w <= 0:
            continue
        g = groups.setdefault(x.assist_level, {"sec": 0, "ped": 0, "rider": 0.0, "motor": 0.0})
        g["sec"] += w
        if x.rider_power_w > ASSIST_MIN_RIDER_W:
            g["ped"] += w
            g["rider"] += x.rider_power_w * w
            g["motor"] += x.motor_power_w * w
    return [
        {"level": level, "name": assist_name(level), "seconds": int(g["sec"]), "seconds_pedaling": int(g["ped"]),
         "factor": round(g["motor"] / g["rider"], 2) if g["ped"] >= 30 else None}
        for level, g in sorted(groups.items())
    ]


def _climbing(p: _Prep, mass: float) -> dict | _Fail:
    runs = _climb_runs(p)
    if not runs:
        return _Fail("no_climb")
    idx = [i for a, b in runs for i in range(a, b)]
    weights = [p.w[i] for i in idx]
    sec = sum(weights)
    gain = dist = 0.0
    for a, b in runs:
        up, down = p.s[b - 1].altitude_m, p.s[a].altitude_m
        if up is not None and down is not None:
            gain += max(0.0, up - down)
        dist += p.dist[b - 1] - p.dist[a]
    total_w = sum(p.s[i].total_power_w * p.w[i] for i in idx) / sec
    rider_w = sum(p.s[i].rider_power_w * p.w[i] for i in idx) / sec
    return {
        "segments": len(runs), "seconds": sec, "mean_grade_pct": round(100 * gain / dist, 1) if dist else None,
        "vam_m_per_h": round(gain / (sec / 3600)), "avg_total_w": round(total_w), "avg_rider_w": round(rider_w),
        "total_w_per_kg": round(total_w / mass, 2), "rider_w_per_kg": round(rider_w / mass, 2),
        "assist": assist_factors([p.s[i] for i in idx], weights),
    }


# ------------------------------------------------------------------ Bedingungen, Hin- und Rückweg


def _cells(points: list[tuple[int, float, float]], origin: tuple[float, float]) -> list[tuple[int, int]]:
    """Rasterzellen (200 m) in Fahrtreihenfolge, hintereinander gleiche Zellen nur einmal.

    Nur relative Lage zum gemeinsamen Ursprung ``origin``; Koordinaten verlassen das Modul nicht.
    """
    lat0, lon0 = origin
    kx = 111320.0 * math.cos(math.radians(lat0))
    out: list[tuple[int, int]] = []
    for _, lat, lon in points:
        c = (math.floor((lon - lon0) * kx / CELL_M), math.floor((lat - lat0) * 110540.0 / CELL_M))
        if not out or out[-1] != c:
            out.append(c)
    return out


def _match(cell: tuple[int, int], order: dict[tuple[int, int], int]) -> int | None:
    """Position der Zelle in der Gegenrichtung (auch Nachbarzellen, GPS springt über Zellgrenzen)."""
    for dx in (0, -1, 1):
        for dy in (0, -1, 1):
            hit = order.get((cell[0] + dx, cell[1] + dy))
            if hit is not None:
                return hit
    return None


def _retraced(short: list[tuple[int, int]], long_: list[tuple[int, int]]) -> bool:
    order = {}
    for i, c in enumerate(long_):
        order.setdefault(c, i)
    hits = [m for m in (_match(c, order) for c in short) if m is not None]
    if len(hits) < LOOP_COVERAGE * len(short):
        return False
    pairs = [(a, b) for a, b in zip(hits, hits[1:]) if a != b]
    return bool(pairs) and sum(1 for a, b in pairs if b < a) / len(pairs) >= LOOP_DIRECTION


def _turn_index(p: _Prep) -> tuple[int, list[tuple[int, float, float]]] | None:
    """Wendepunkt = am weitesten vom Start entfernter GPS-Punkt; None ohne brauchbare Spur."""
    pts = [(i, x.latitude, x.longitude) for i, x in enumerate(p.s) if x.latitude is not None and x.longitude is not None]
    if len(pts) < MIN_SECONDS:
        return None
    lat0, lon0 = pts[0][1], pts[0][2]
    kx = 111320.0 * math.cos(math.radians(lat0))
    far = max(range(len(pts)), key=lambda k: math.hypot((pts[k][2] - lon0) * kx, (pts[k][1] - lat0) * 110540.0))
    return far, pts


def detect_out_and_back(p: _Prep) -> int | None:
    """Sample-Index des Wendepunkts, wenn die Fahrt dieselbe Strecke hin und zurück fährt; sonst None."""
    found = _turn_index(p)
    if not found:
        return None
    far, pts = found
    origin = (pts[0][1], pts[0][2])
    first, second = _cells(pts[: far + 1], origin), _cells(pts[far:], origin)
    if min(len(first), len(second)) < LOOP_MIN_CELLS:
        return None
    short, long_ = (first, second) if len(first) <= len(second) else (second, first)
    return pts[far][0] if _retraced(short, long_) else None


def _out_and_back(p: _Prep, obs: list[tuple], res: tuple | None) -> dict:
    if res is None:
        return {"detected": False, "reason": "no_resistance_estimate"}
    turn = detect_out_and_back(p)
    if turn is None:
        return {"detected": False}
    cda = res[1]
    halves = ([o for o in obs if o[0] <= turn], [o for o in obs if o[0] > turn])
    crrs = []
    for half in halves:
        if sum(o[2] for o in half) < MIN_SECONDS:
            return {"detected": False, "reason": "too_little_valid_data"}
        crrs.append(_fit1(_sums(half), cda))
    return {"detected": True, "crr_out": round(crrs[0], 4), "crr_back": round(crrs[1], 4), "crr_mean": round(sum(crrs) / 2, 4)}


def _conditions(p: _Prep, obs: list[tuple], res: tuple | None, device_temp: bool) -> dict:
    temps = [x.temperature_c for x in p.s if x.temperature_c is not None]
    return {
        "avg_temp_c": round(sum(temps) / len(temps), 1) if temps else None,
        "min_temp_c": round(min(temps), 1) if temps else None,
        "max_temp_c": round(max(temps), 1) if temps else None,
        "temp_note": "device_temperature" if device_temp else None,
        "out_and_back": _out_and_back(p, obs, res),
    }


# ------------------------------------------------------------------ Einstieg


def _warnings(p: _Prep, res_block: dict | _Fail, rho_source: str, device_temp: bool) -> list[str]:
    out = []
    if len(p.s) < FIT_MIN_SECONDS:
        out.append("few_samples")
    if isinstance(res_block, dict) and res_block["cda_source"] == "assumed":
        out.append("cda_assumed")
    if isinstance(res_block, dict) and not CRR_RANGE[0] <= res_block["crr"] <= CRR_RANGE[1]:
        out.append("crr_out_of_range")
    if rho_source == "default":
        out.append("rho_default")
    if device_temp:
        out.append("temp_is_device")
    if not any(x.altitude_m is not None for x in p.s):
        out.append("no_altitude")
    if not any(x.latitude is not None for x in p.s):
        out.append("no_gps")
    return out


def analyze_physics(samples: list[Sample], mass_kg: float, cda: float = DEFAULT_CDA, eta: float = DEFAULT_ETA,
                    battery_wh: float | None = None) -> dict:
    """Fahrphysik einer Fahrt als JSON-taugliches dict.

    ``mass_kg``: Systemgewicht (Fahrer + Ausrüstung + Bike). ``cda``: Vorgabe, falls sich CdA nicht fitten lässt.
    ``battery_wh``: Akkukapazität, nur für die Akku-Bilanz. Blöcke ohne Datengrundlage sind ``None`` mit ``*_reason``.
    """
    if not samples:
        return {"version": PHYSICS_VERSION, "resistance": None, "resistance_reason": "no_samples", "energy": None,
                "energy_reason": "no_samples", "climbing": None, "climbing_reason": "no_samples", "conditions": None,
                "warnings": ["few_samples"], "is_estimate": True}
    p = prepare(samples)
    rho, rho_source, avg_temp = _air_density(samples)
    device_temp = _temp_is_device(samples)
    res_block, obs, res = _resistance(p, mass_kg, cda, eta, rho, rho_source, avg_temp)
    energy = _energy(p, mass_kg, eta, rho, res, battery_wh)
    climbing = _climbing(p, mass_kg)

    out: dict = {"version": PHYSICS_VERSION, "is_estimate": True,
                 "inputs": {"mass_kg": mass_kg, "cda_default": cda, "eta": eta, "battery_wh": battery_wh}}
    for name, block in (("resistance", res_block), ("energy", energy), ("climbing", climbing)):
        out[name] = None if isinstance(block, _Fail) else block
        if isinstance(block, _Fail):
            out[f"{name}_reason"] = block.reason
    out["conditions"] = _conditions(p, obs, res, device_temp)
    out["warnings"] = _warnings(p, res_block, rho_source, device_temp)
    return out


# ------------------------------------------------------------------ Vergleich über Fahrten

COMPARE_KEYS = ("crr", "cda", "flat_power_w_25", "wh_per_km", "motor_share_pct", "climb_rider_w_per_kg", "avg_temp_c")
OUTLIER_FACTOR = 1.25
"""Rollwiderstand über dem 1,25-fachen Median: Hinweis auf schleifende Bremse oder Ähnliches."""
OUTLIER_MIN_RIDES = 3


def compare_item(result: dict) -> dict:
    """Kernwerte einer Fahrt für den Vergleich (fehlende Blöcke ergeben ``None``)."""
    res, energy, climb = result.get("resistance") or {}, result.get("energy") or {}, result.get("climbing") or {}
    return {
        "crr": res.get("crr"), "cda": res.get("cda"), "cda_source": res.get("cda_source"),
        "flat_power_w_25": (res.get("flat_power_w") or {}).get("25", {}).get("w"),
        "wh_per_km": energy.get("wh_per_km"), "motor_share_pct": energy.get("motor_share_pct"),
        "climb_rider_w_per_kg": climb.get("rider_w_per_kg"),
        "avg_temp_c": (result.get("conditions") or {}).get("avg_temp_c"),
        "temp_is_device": "temp_is_device" in result.get("warnings", []),
    }


def _median(values: list[float]) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def median_of(items: list[dict]) -> dict:
    """Median je Kennwert über alle Fahrten, die ihn haben."""
    out = {}
    for key in COMPARE_KEYS:
        med = _median([i[key] for i in items if i.get(key) is not None])
        out[key] = None if med is None else round(med, 4 if key == "crr" else 2)
    return out


def mark_outliers(items: list[dict]) -> list[dict]:
    """Setzt ``outlier`` (Crr > 1,25 × Median); erst ab drei Fahrten mit Crr aussagekräftig."""
    crrs = [i["crr"] for i in items if i.get("crr") is not None]
    med = _median(crrs) if len(crrs) >= OUTLIER_MIN_RIDES else None
    for i in items:
        i["outlier"] = bool(med and i.get("crr") is not None and i["crr"] > OUTLIER_FACTOR * med)
    return items
