import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest


@pytest.fixture(autouse=True)
def no_geocoding(monkeypatch):
    """Tests gehen nie ins Netz: Ortsnamen-Dienst global aus."""
    monkeypatch.setenv("AVINOX_GEOCODE_URL", "off")


def web_login(client, user: str, password: str) -> None:
    """Meldet den Test-Client über das Login-Formular an (Session-Cookie; Seiten akzeptieren kein Basic)."""
    import re

    html = client.get("/login").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    r = client.post("/login", data={"username": user, "password": password, "next": "/", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303, r.text
