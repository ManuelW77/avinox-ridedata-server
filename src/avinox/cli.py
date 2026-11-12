"""Kommandozeile: ``avinox summary <datei|ordner> ... [--json]``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ride_rec import RideRecError, parse_file
from .summary import summarize


def _ride_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        files.extend(sorted(p.rglob("ride_rec_*.proto")) if p.is_dir() else [p])
    return files


def _fmt_duration(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}"


def _num(value: float | None, digits: int = 0) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def cmd_summary(args: argparse.Namespace) -> int:
    rows = []
    failed = 0
    for path in _ride_files(args.paths):
        try:
            ride = parse_file(path)
        except (OSError, RideRecError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            failed += 1
            continue
        for warning in ride.warnings:
            print(f"{path}: {warning}", file=sys.stderr)
        rows.append((path, summarize(ride)))

    if args.json:
        print(json.dumps([{"file": str(p), **s.to_dict()} for p, s in rows], indent=2, ensure_ascii=False))
        return 1 if failed else 0

    head = f"{'Datei':<34} {'Start (UTC)':<16} {'Dauer':>8} {'Fahrt':>8} {'km':>6} {'Hm':>5} {'Ø km/h':>6} {'Ø Fahrer W':>10} {'Ø Motor W':>9} {'Ø U/min':>7} {'Akku %':>7} {'Motor Wh':>8}"
    print(head)
    print("-" * len(head))
    for path, s in rows:
        start = s.start_time.strftime("%Y-%m-%d %H:%M") if s.start_time else "ohne Uhrzeit"
        battery = f"{_num(s.battery_start_pct)}→{_num(s.battery_end_pct)}"
        print(
            f"{str(path)[-34:]:<34} {start:<16} {_fmt_duration(s.duration_s):>8} {_fmt_duration(s.moving_time_s):>8} "
            f"{s.distance_km:>6.2f} {_num(s.ascent_m):>5} {_num(s.avg_speed_kmh, 1):>6} {_num(s.avg_rider_power_w):>10} "
            f"{_num(s.avg_motor_power_w):>9} {_num(s.avg_cadence_rpm):>7} {battery:>7} {s.motor_energy_wh:>8.0f}"
        )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="avinox", description="Avinox-Fahrtdaten auswerten")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sum = sub.add_parser("summary", help="Kennzahlen je Fahrt anzeigen")
    p_sum.add_argument("paths", nargs="+", help="ride_rec_*.proto-Dateien oder Ordner")
    p_sum.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    p_sum.set_defaults(func=cmd_summary)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
