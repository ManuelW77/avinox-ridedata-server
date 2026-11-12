"""Kommandozeile.

* ``avinox summary <datei|ordner> ... [--json]`` – Kennzahlen je Fahrt
* ``avinox analyze <datei> [--buried datei]`` – vollständige Auswertung als JSON
* ``avinox gpx <datei> [-o ziel]`` / ``avinox geojson <datei> [-o ziel]`` – Strecke
* ``avinox device <buried_file_cur.json>`` – Geräte- und Sessiondaten
* ``avinox serve [--host H] [--port P] [--data-dir DIR]`` – Server starten
* ``avinox import <laufwerk-ordner> [--data-dir DIR]`` – Laufwerkskopie in den Server-Datenordner übernehmen
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import analyze
from .buried import device_info, parse_buried_file
from .hms import parse_hms_file
from .ride_rec import RideRecError, parse_file
from .summary import summarize
from .track import SIMPLIFY_TOLERANCE_M, to_geojson, to_gpx


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

    head = (
        f"{'Datei':<34} {'Start (UTC)':<16} {'Dauer':>8} {'Fahrt':>8} {'km':>6} {'Hm':>5} {'Ø km/h':>6} "
        f"{'Ø Fahrer W':>10} {'Ø Motor W':>9} {'Ø U/min':>7} {'Akku %':>7} {'kcal':>5}"
    )
    print(head)
    print("-" * len(head))
    for path, s in rows:
        start = s.start_time.strftime("%Y-%m-%d %H:%M") if s.start_time else "ohne Uhrzeit"
        battery = f"{_num(s.battery_start_pct)}→{_num(s.battery_end_pct)}"
        print(
            f"{str(path)[-34:]:<34} {start:<16} {_fmt_duration(s.duration_s):>8} {_fmt_duration(s.moving_time_s):>8} "
            f"{s.distance_km:>6.2f} {s.ascent_m:>5.0f} {s.avg_speed_kmh:>6.1f} {s.avg_rider_power_w:>10.0f} "
            f"{s.avg_motor_power_w:>9.0f} {s.avg_cadence_rpm:>7.0f} {battery:>7} {s.calories_kcal:>5.0f}"
        )
    return 1 if failed else 0


def find_buried(ride_path: Path) -> Path | None:
    """Sucht ``buried_rec/buried_file_cur.json`` oberhalb der Fahrtdatei (Laufwerkswurzel)."""
    for parent in ride_path.resolve().parents:
        candidate = parent / "buried_rec" / "buried_file_cur.json"
        if candidate.is_file():
            return candidate
    return None


def _write(text: str, target: str | None) -> None:
    if target:
        Path(target).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _load(path: str):
    try:
        return parse_file(path)
    except (OSError, RideRecError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return None


def cmd_analyze(args: argparse.Namespace) -> int:
    ride = _load(args.path)
    if ride is None:
        return 1
    buried = Path(args.buried) if args.buried else find_buried(Path(args.path))
    sessions = parse_buried_file(buried) if buried and buried.is_file() else None
    names = {}
    for item in args.mode_name or []:
        level, _, name = item.partition("=")
        if not level.isdigit() or not name:
            print(f"--mode-name erwartet NUMMER=NAME, nicht {item!r}", file=sys.stderr)
            return 2
        names[int(level)] = name
    result = analyze(ride, sessions, custom_names=names or None)
    _write(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False) + "\n", args.output)
    return 0


def cmd_gpx(args: argparse.Namespace) -> int:
    ride = _load(args.path)
    if ride is None:
        return 1
    _write(to_gpx(ride), args.output)
    return 0


def cmd_geojson(args: argparse.Namespace) -> int:
    ride = _load(args.path)
    if ride is None:
        return 1
    _write(json.dumps(to_geojson(ride, tolerance_m=args.tolerance), ensure_ascii=False) + "\n", args.output)
    return 0


def cmd_device(args: argparse.Namespace) -> int:
    sessions = parse_buried_file(args.path)
    out = {
        **device_info(sessions),
        "sessions": [
            {
                "boot_count": s.boot_count,
                "end_time": s.end_time.isoformat() if s.end_time else None,
                "power_on_s": s.power_on_s,
                "rides": len(s.rides),
                "battery": s.battery,
                "thermal_limit": s.thermal_limit,
            }
            for s in sessions
        ],
    }
    hms = Path(args.path).resolve().parent.parent / "hms_rec" / "hms_current_storage.proto"
    if hms.is_file():
        out["hms"] = [{"code": e.code, "time": e.time.isoformat() if e.time else None} for e in parse_hms_file(hms)]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    from .server.app import create_app
    from .server.config import load_settings

    settings = load_settings(args.data_dir)
    if os.environ.get("AVINOX_UPLOAD_TOKEN", "").strip():
        print("Upload-Token: aus Umgebungsvariable AVINOX_UPLOAD_TOKEN")
    else:
        print(f"Upload-Token: steht in {settings.data_dir / 'upload_token'}")
    print(f"Datenordner: {settings.data_dir}")
    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from .server.config import load_settings
    from .server.store import Store

    root = Path(args.path)
    if not root.is_dir():
        print(f"{root}: kein Ordner", file=sys.stderr)
        return 1
    store = Store(load_settings(args.data_dir))
    try:
        counts = store.import_directory(root)
        rides = len(store.list_rides())
    finally:
        store.close()
    print(f"neu: {counts['new']}, bekannt: {counts['known']}, übersprungen: {counts['skipped']}")
    print(f"Fahrten: {rides}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="avinox", description="Avinox-Fahrtdaten auswerten")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sum = sub.add_parser("summary", help="Kennzahlen je Fahrt anzeigen")
    p_sum.add_argument("paths", nargs="+", help="ride_rec_*.proto-Dateien oder Ordner")
    p_sum.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    p_sum.set_defaults(func=cmd_summary)

    p_an = sub.add_parser("analyze", help="vollständige Auswertung einer Fahrt als JSON")
    p_an.add_argument("path")
    p_an.add_argument("--buried", help="buried_file_cur.json (Standard: auf dem Laufwerk suchen)")
    p_an.add_argument("-o", "--output")
    p_an.add_argument("--pretty", action="store_true")
    p_an.add_argument("--mode-name", action="append", metavar="NUMMER=NAME", help="eigenen Modus benennen, z. B. 21=Tour")
    p_an.set_defaults(func=cmd_analyze)

    p_gpx = sub.add_parser("gpx", help="Strecke als GPX")
    p_gpx.add_argument("path")
    p_gpx.add_argument("-o", "--output")
    p_gpx.set_defaults(func=cmd_gpx)

    p_geo = sub.add_parser("geojson", help="vereinfachte Strecke als GeoJSON")
    p_geo.add_argument("path")
    p_geo.add_argument("-o", "--output")
    p_geo.add_argument("--tolerance", type=float, default=SIMPLIFY_TOLERANCE_M, help="Vereinfachung in Metern")
    p_geo.set_defaults(func=cmd_geojson)

    p_dev = sub.add_parser("device", help="Geräte- und Sessiondaten aus buried_file_cur.json")
    p_dev.add_argument("path")
    p_dev.set_defaults(func=cmd_device)

    p_srv = sub.add_parser("serve", help="Server (Upload-API und Weboberfläche) starten")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)
    p_srv.add_argument("--data-dir", help="Datenordner (Standard: AVINOX_DATA_DIR oder ./data)")
    p_srv.set_defaults(func=cmd_serve)

    p_imp = sub.add_parser("import", help="Kopie des Display-Laufwerks importieren")
    p_imp.add_argument("path", help="Laufwerks-Ordner")
    p_imp.add_argument("--data-dir", help="Datenordner (Standard: AVINOX_DATA_DIR oder ./data)")
    p_imp.set_defaults(func=cmd_import)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
