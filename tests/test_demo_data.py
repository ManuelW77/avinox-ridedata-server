"""Prüft gegen die lokale Kopie des Display-Laufwerks, falls vorhanden (nicht im Repo)."""

from pathlib import Path

import pytest

from avinox.ride_rec import parse_file
from avinox.summary import summarize

DEMO = Path(__file__).parents[1] / "_demo-data" / "ride_rec"
FILES = sorted(DEMO.rglob("ride_rec_*.proto")) if DEMO.is_dir() else []

pytestmark = pytest.mark.skipif(not FILES, reason="_demo-data nicht vorhanden")


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(DEMO)))
def test_demo_ride_parses_cleanly(path):
    ride = parse_file(path)
    assert ride.warnings == []
    assert len(ride.samples) >= ride.header.sample_count > 0
    # Distanz im Header = letzte Sample-Distanz; Kilometerstand läuft mit
    assert ride.samples[-1].distance_m == ride.header.distance_m
    odo = ride.samples[-1].odometer_m - ride.samples[0].odometer_m
    assert abs(odo - ride.header.distance_m) <= 10
    summary = summarize(ride)
    assert 0 < summary.moving_time_s <= summary.duration_s
