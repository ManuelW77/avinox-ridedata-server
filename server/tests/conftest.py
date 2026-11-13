import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest


@pytest.fixture(autouse=True)
def no_geocoding(monkeypatch):
    """Tests gehen nie ins Netz: Ortsnamen-Dienst global aus."""
    monkeypatch.setenv("AVINOX_GEOCODE_URL", "off")
