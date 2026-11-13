"""Akku-Gesundheit: Kennzahlen und Hinweise aus den Einschalt-Aufzeichnungen (buried) und den Fahrten.

Reine Funktionen ohne Datenbank. Eingabe sind Sessions in zeitlicher Reihenfolge (Dateireihenfolge).
Eigenheiten der Display-Daten: ``start_pct == 0`` zusammen mit ``start_temp_c == 0`` heißt „unbekannt“,
Zeiten vor 2020 sind unbekannt (Uhr nicht gestellt), der Display-Zykluszähler ist unbrauchbar.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median

CHARGE_MIN_RISE = 2  # Prozentpunkte über dem letzten Stand, ab denen wir „geladen“ sagen
REST_FULL_MV = 4050
REST_MAX_A = 2
BALANCE_WARN_MV = 15
COLD_C = 5
HOT_C = 45
DEEP_PCT = 10
DEEP_CELL_MV = 3300
STORAGE_PCT = 98
STORAGE_DAYS = 3
SAG_MIN_A = 10
SOH_MIN_POINTS = 10
SOH_GROUP = 5
SOH_MIN_USED_PCT = 10  # bei wenig Verbrauch verfälscht die 1-%-Auflösung den Wert stark
SOH_WARN_PCT = -10


def _time(value) -> datetime | None:
    """ISO-Text -> datetime; unbekannt (None, Fehler, Jahr < 2020) -> None."""
    if not value:
        return None
    try:
        t = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t if t.year >= 2020 else None


def _iso(value) -> str | None:
    t = _time(value)
    return str(value) if t else None


def _num(s: dict, key: str) -> float:
    v = s.get(key)
    return v if isinstance(v, (int, float)) else 0


def _start_unknown(s: dict) -> bool:
    return s.get("start_pct") is None or (not s.get("start_pct") and not s.get("start_temp_c"))


def _end_known(s: dict) -> bool:
    return s.get("end_pct") is not None


def _hot_seconds(temp_s: dict | None) -> float:
    """Sekunden im Bereich ab 50 °C (Schlüssel = Untergrenze, ``>60`` = über 60)."""
    total = 0.0
    for band, secs in (temp_s or {}).items():
        digits = str(band).lstrip("<>").strip()
        try:
            low = float(digits)
        except ValueError:
            continue
        if str(band).startswith("<"):
            continue
        if low >= 50 and isinstance(secs, (int, float)):
            total += secs
    return total


def _charges(ss: list[dict]) -> list[dict]:
    out = []
    for a, b in zip(ss, ss[1:]):
        if not _end_known(a):
            continue
        end_a = a["end_pct"]
        if not _start_unknown(b):
            if b["start_pct"] > end_a + CHARGE_MIN_RISE:
                temp = b.get("start_temp_c")
                out.append(
                    {
                        "from_pct": end_a,
                        "to_pct": b["start_pct"],
                        "time": _iso(b.get("time")),
                        "temp_c": temp if temp else None,
                    }
                )
        elif _end_known(b) and b["end_pct"] > end_a + CHARGE_MIN_RISE:
            out.append({"from_pct": end_a, "to_pct": None, "min_to_pct": b["end_pct"], "time": _iso(b.get("time")), "temp_c": None})
    return out


def _discharged(ss: list[dict]) -> float:
    levels = []
    for s in ss:
        if not _start_unknown(s):
            levels.append(s["start_pct"])
        if _end_known(s) and not (_start_unknown(s) and not s["end_pct"]):
            levels.append(s["end_pct"])
    return sum(p - c for p, c in zip(levels, levels[1:]) if c < p)


def health(sessions_battery: list[dict], rides: list[dict]) -> dict:
    ss = sessions_battery or []
    charges = _charges(ss)
    discharged = _discharged(ss)

    rest = [
        {"time": _iso(s.get("time")), "delta_mv": s["max_cell_delta_mv"]}
        for s in ss
        if _num(s, "max_cell_mv") >= REST_FULL_MV and _num(s, "max_current_a") <= REST_MAX_A and s.get("max_cell_delta_mv") is not None
    ]
    rest_balance = {
        "points": rest,
        "first_mv": rest[0]["delta_mv"] if rest else None,
        "latest_mv": rest[-1]["delta_mv"] if rest else None,
    }

    cold = hot = limit = 0
    for s in ss:
        known = _num(s, "start_temp_c") > 0 or _num(s, "end_temp_c") > 0
        if known and _num(s, "max_temp_c") < COLD_C:
            cold += 1
        if _num(s, "max_temp_c") >= HOT_C:
            hot += 1
        if s.get("thermal_limit"):
            limit += 1
    temperature = {
        "cold_sessions": cold,
        "hot_sessions": hot,
        "seconds_over_50": round(sum(_hot_seconds(s.get("temp_s")) for s in ss)),
        "thermal_limit_sessions": limit,
    }

    deep = [s for s in ss if _end_known(s) and s["end_pct"] <= DEEP_PCT and not (_start_unknown(s) and not s["end_pct"])]
    deep_times = [t for t in (_iso(s.get("time")) for s in deep) if t]
    deep_discharge = {
        "count": len(deep),
        "last_time": deep_times[-1] if deep_times else None,
        "low_cell_count": sum(1 for s in ss if 0 < _num(s, "min_cell_mv") < DEEP_CELL_MV),
    }

    cold_charges = [c for c in charges if c["temp_c"] and c["temp_c"] > 0 and c["temp_c"] < COLD_C]

    storage = []
    for a, b in zip(ss, ss[1:]):
        ta, tb = _time(a.get("time")), _time(b.get("time"))
        if not ta or not tb or not _end_known(a) or _start_unknown(b):
            continue
        days = (tb - ta).total_seconds() / 86400
        if a["end_pct"] >= STORAGE_PCT and b["start_pct"] >= STORAGE_PCT and days > STORAGE_DAYS:
            storage.append({"days": round(days, 1), "time": b["time"]})

    sag = [
        {"time": _iso(s.get("time")), "mv_per_a": round((s["max_cell_mv"] - s["min_cell_mv"]) / s["max_current_a"], 1)}
        for s in ss
        if _num(s, "max_current_a") >= SAG_MIN_A and _num(s, "max_cell_mv") > 0 and _num(s, "min_cell_mv") > 0
    ]

    soh_points = [
        {"time": r.get("time"), "wh": round(r["motor_wh_per_pct"] * 100)}
        for r in rides or []
        if r.get("motor_wh_per_pct") and (r.get("used_pct") is None or r["used_pct"] >= SOH_MIN_USED_PCT)
    ]
    change = first = last = None
    if len(soh_points) >= 2:
        group = min(SOH_GROUP, len(soh_points) // 2)
        first = round(median(p["wh"] for p in soh_points[:group]))
        last = round(median(p["wh"] for p in soh_points[-group:]))
        if len(soh_points) >= SOH_MIN_POINTS and first:
            change = round((last / first - 1) * 100, 1)

    hints = []
    if deep_discharge["count"]:
        hints.append({"level": "warn", "key": "deep", "params": {"n": deep_discharge["count"]}})
    elif deep_discharge["low_cell_count"]:
        hints.append({"level": "warn", "key": "lowcell", "params": {"n": deep_discharge["low_cell_count"]}})
    if rest_balance["latest_mv"] is not None and rest_balance["latest_mv"] > BALANCE_WARN_MV:
        hints.append({"level": "warn", "key": "balance", "params": {"mv": rest_balance["latest_mv"]}})
    if change is not None and change < SOH_WARN_PCT:
        hints.append({"level": "warn", "key": "soh", "params": {"pct": abs(change)}})
    if cold_charges:
        hints.append({"level": "info", "key": "coldcharge", "params": {"n": len(cold_charges)}})
    if storage:
        hints.append({"level": "info", "key": "storage", "params": {"n": len(storage), "days": max(x["days"] for x in storage)}})
    if hot:
        hints.append({"level": "warn", "key": "hot", "params": {"n": hot}})
    if cold:
        hints.append({"level": "info", "key": "cold", "params": {"n": cold}})
    if limit:
        hints.append({"level": "info", "key": "limit", "params": {"n": limit}})
    if not hints:
        hints.append({"level": "ok", "key": "ok", "params": {}})

    return {
        "charges": charges,
        "charge_count": len(charges),
        "full_cycles": round(discharged / 100, 1),
        "discharged_pct": round(discharged),
        "rest_balance": rest_balance,
        "temperature": temperature,
        "deep_discharge": deep_discharge,
        "cold_charge_suspects": cold_charges,
        "full_storage": storage,
        "sag": sag,
        "soh": {"points": soh_points, "first_wh": first, "last_wh": last, "change_pct": change},
        "hints": hints,
    }
