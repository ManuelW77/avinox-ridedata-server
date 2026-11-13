"""KI-Auswertung: LLM-Aufruf wird immer ersetzt, es gibt nie echtes Netz. Nur synthetische Daten, Fake-Schlüssel."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from avinox.server import ai
from avinox.server.app import create_app
from avinox.server.config import load_settings
from avinox.server.db import Database
from avinox.server.store import Store
from ridebuilder import ride
from test_track import T0, gps_sample

KEY = "sk-fake-1234abcd"
GOOD = {"summary": "Eine **schöne** Runde.", "drive": "Viel Gang 5.", "power": "Ordentlich.", "hill": "Flach.", "compare": "Etwas länger.",
        "bests": "x", "trend": "y", "tips": ["Früher schalten.", "- Kadenz 80 anpeilen.", "", "c", "d", "e"]}


def ride_bytes(n=400, gear=5, cadence_rpm=45, start=T0, ride_id=1):
    samples = [{**gps_sample(t, 5 * t), 22: gear if t % 100 < 90 else gear + 1, 9: cadence_rpm * 100, 8: 2, 71: 90 - t // 100, 51: 140}
               for t in range(n)]
    return ride(samples, start=start, end=start + n, distance=5 * n, ride_id=ride_id)


class FakeLLM:
    def __init__(self, reply=None):
        self.reply = reply if reply is not None else json.dumps(GOOD)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    # Sicherheitsnetz: jede echte Netzanfrage schlägt fehl
    monkeypatch.setattr(ai, "_post", lambda *a, **k: pytest.fail("echtes Netz"))
    app = create_app(load_settings(tmp_path / "data"))
    app.state.ai_paused = True  # Tests treiben die Warteschlange selbst (run_queue); Worker-Tests geben sie frei
    with TestClient(app) as c:
        yield c


@pytest.fixture
def store(client):
    return client.app.state.store


def configure(client, **extra):
    body = {"enabled": True, "provider": "openrouter", "model": "m-1", "api_key": KEY, **extra}
    r = client.put("/api/v1/settings/ai", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def add_ride(store, **kw):
    store.add_file(f"ride_rec/4242/ride_rec_{kw.get('ride_id', 1)}.proto", ride_bytes(**kw))
    return store.list_rides()[0]["id"]


def keys_deep(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k).lower()
            yield from keys_deep(v)
    elif isinstance(o, list):
        for v in o:
            yield from keys_deep(v)


# ------------------------------------------------------------------ Einstellungen


def test_settings_never_return_key(client):
    s = client.get("/api/v1/settings/ai").json()
    assert s["enabled"] is False and s["has_key"] is False and s["key_hint"] is None and s["lang"] == "de"
    assert {p["id"] for p in s["presets"]} >= {"openrouter", "openai", "anthropic", "mistral", "groq", "ollama", "custom"}
    assert s["counts"] == {"done": 0, "errors": 0, "pending": 0} and s["busy"] is False and s["last_error"] is None
    r = client.put("/api/v1/settings/ai", json={"api_key": KEY, "provider": "openai"})
    assert r.json()["has_key"] is True and r.json()["key_hint"] == "…abcd"
    assert r.json()["base_url"] == "https://api.openai.com/v1" and r.json()["model"] == "gpt-4o-mini"
    for resp in (r, client.get("/api/v1/settings/ai"), client.put("/api/v1/settings/ai", json={"model": "x"})):
        assert KEY not in resp.text and "api_key" not in resp.json()
    assert KEY not in client.get("/settings").text


def test_api_key_missing_unchanged_and_empty_deletes(client):
    configure(client)
    assert client.put("/api/v1/settings/ai", json={"model": "m-2"}).json()["key_hint"] == "…abcd"
    r = client.put("/api/v1/settings/ai", json={"api_key": ""})
    assert r.json()["has_key"] is False and r.json()["key_hint"] is None


def test_settings_type_errors(client):
    for body in ({"enabled": "yes"}, {"model": 5}, {"provider": "nope"}, {"lang": "fr"}, {"base_url": "ftp://x"}, {"api_key": 1}):
        assert client.put("/api/v1/settings/ai", json=body).status_code == 400
    assert client.put("/api/v1/settings/ai", json={"base_url": "http://localhost:1234/v1/"}).json()["base_url"] == "http://localhost:1234/v1"


def test_off_without_setup(client, store):
    pk = add_ride(store)
    assert client.get(f"/api/v1/ai/ride/{pk}").json() == {"status": "off", "content": None, "model": None, "created_at": None, "error": None}
    client.put("/api/v1/settings/ai", json={"enabled": True})  # ohne Schlüssel/Modell: nicht eingerichtet
    assert client.get(f"/api/v1/ai/ride/{pk}").json()["status"] == "off"
    assert client.get("/api/v1/ai/dashboard?period=all").json()["status"] == "off"
    assert client.post("/api/v1/settings/ai/fill").json()["queued"] == 0


def test_ollama_needs_no_key(client):
    r = client.put("/api/v1/settings/ai", json={"enabled": True, "provider": "ollama"})
    assert r.json()["has_key"] is False and ai.is_active(client.app.state.store.db)


# ------------------------------------------------------------------ Fakten


def test_ride_facts_content_and_privacy(store):
    store.set_ride_name(add_ride(store), "Geheimer Hausberg")
    store.db.execute("UPDATE ride_meta SET places_json = ? WHERE 1", (json.dumps({"start": "Ortsname X"}),))
    pk = store.list_rides()[0]["id"]
    f = ai.ride_facts(store, pk)
    assert f["distance_km"] == 2.0 and f["gears"]["shifts_total"] == 7 and f["gears"]["highest_gear_number"] == 6
    assert f["gears"]["minutes_per_gear"] and f["gears"]["avg_cadence_rpm_per_gear"]["5"] == 45
    assert f["gears"]["heaviest_3_gears_time_pct"] == 100.0 and f["gears"]["lightest_3_gears_time_pct"] == 0.0
    assert f["low_cadence_while_pedaling_pct"] == 100.0
    assert f["minutes_per_assist_mode"] and f["battery"]["start_pct"] == 90
    assert "compare" not in f  # keine Vergleichsfahrten
    text = json.dumps(f, ensure_ascii=False).lower()
    blocked = ("lat", "lon", "latitude", "longitude", "track", "geojson", "places", "name", "path", "file", "serial", "user", "owner")
    assert not set(keys_deep(f)) & set(blocked)
    for needle in ("geheimer", "ortsname", "testserial", "ride_rec", ".proto", "4242"):
        assert needle not in text
    assert ai.input_hash(f, "a") != ai.input_hash(f, "b")


def test_ride_facts_compare_rank(store):
    add_ride(store, ride_id=1, n=300)
    add_ride(store, ride_id=2, n=500, start=T0 + 10_000)
    pk = max(r["id"] for r in store.list_rides())
    f = ai.ride_facts(store, pk)["compare"]
    assert f["other_rides_count"] == 1 and f["rank"]["distance_km"] == {"position": 1, "of": 2}
    assert f["other_rides_average"]["distance_km"] == 1.5


def test_period_facts_privacy_and_none(store):
    pk = add_ride(store)
    store.set_ride_name(pk, "Geheimer Hausberg")
    dash = store.dashboard("all", 0, lang="de")
    f = ai.period_facts(dash)
    assert f["current"]["rides"] == 1 and f["distribution"]["minutes_per_gear"] and f["bests"]
    assert not set(keys_deep(f)) & ai.BLOCKED_KEYS
    assert "geheimer" not in json.dumps(f, ensure_ascii=False).lower()
    assert ai.period_facts(store.dashboard("week", 0, lang="de", today=__import__("datetime").date(2020, 1, 1))) is None


def test_clean_is_recursive():
    out = ai.clean({"a": 1, "lat": 5, "x": [{"name": "n", "ok": 2, "n": None}], "y": {"geojson": {}, "z": 3}})
    assert out == {"a": 1, "x": [{"ok": 2}], "y": {"z": 3}}


# ------------------------------------------------------------------ Parser


def test_parse_json_tolerant():
    assert ai.parse_json('{"a": 1}') == {"a": 1}
    assert ai.parse_json('Hier:\n```json\n{"a": "x"}\n```\nviel Spaß') == {"a": "x"}
    assert ai.parse_json('Text {"a": {"b": 2}} Rest {"c": 1}') == {"a": {"b": 2}}
    for bad in ("", "kein json", '{"a": ', "[1, 2]"):
        with pytest.raises(ai.AiError) as e:
            ai.parse_json(bad)
        assert e.value.code == "bad_response"


def test_normalize_keys_plain_text_and_tips():
    n = ai.normalize(GOOD, "ride")
    assert set(n) == {"summary", "drive", "power", "hill", "compare", "tips"}
    assert n["summary"] == "Eine schöne Runde." and n["tips"][:2] == ["Früher schalten.", "Kadenz 80 anpeilen."] and len(n["tips"]) == 4
    p = ai.normalize({"summary": "s", "tips": "ein Tipp"}, "period")
    assert set(p) == {"summary", "drive", "power", "hill", "bests", "trend", "tips"} and p["tips"] == ["ein Tipp"] and p["bests"] == ""


def test_messages_contain_keys_and_no_personal_data(store):
    pk = add_ride(store)
    msgs = ai.build_messages("ride", ai.ride_facts(store, pk), "de")
    assert msgs[0]["role"] == "system" and '"compare"' in msgs[0]["content"] and "JSON" in msgs[0]["content"]
    assert "Du" in msgs[0]["content"] and "kein Markdown" in msgs[0]["content"]
    assert "Markdown" in ai.build_messages("period", {"a": 1}, "en")[0]["content"] and '"trend"' in ai.build_messages("period", {}, "en")[0]["content"]


# ------------------------------------------------------------------ LLM-Aufruf (HTTP ersetzt)


def test_chat_request_shape_and_400_retry(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    ai.update_settings(db, {"enabled": True, "provider": "openrouter", "model": "m-1", "api_key": KEY})
    calls = []

    def fake(url, headers, body, timeout):
        calls.append((url, headers, dict(body), timeout))
        if len(calls) == 1:
            return 400, {"error": {"message": "response_format unsupported"}}
        return 200, {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(ai, "_post", fake)
    assert ai.chat(db, [{"role": "user", "content": "hi"}]) == "{}"
    url, headers, body, timeout = calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions" and headers["Authorization"] == f"Bearer {KEY}"
    assert headers["X-Title"] == "Avinox Ride Data" and timeout == 90 and body["temperature"] == 0.4
    assert body["response_format"] == {"type": "json_object"} and "response_format" not in calls[1][2] and len(calls) == 2
    db.close()


@pytest.mark.parametrize("status,code", [(401, "invalid_key"), (404, "bad_url"), (429, "rate_limited"), (500, "http_500")])
def test_chat_error_codes(tmp_path, monkeypatch, status, code):
    db = Database(tmp_path / "t.db")
    ai.update_settings(db, {"enabled": True, "provider": "openai", "model": "m", "api_key": KEY})
    monkeypatch.setattr(ai, "_post", lambda *a: (status, {"error": {"message": "nope"}}))
    with pytest.raises(ai.AiError) as e:
        ai.chat(db, [])
    assert e.value.code == code and KEY not in str(e.value)
    monkeypatch.setattr(ai, "_post", lambda *a: (200, {"choices": []}))
    with pytest.raises(ai.AiError) as e:
        ai.chat(db, [])
    assert e.value.code == "bad_response"
    db.close()


def test_test_route_statuses(client, monkeypatch):
    assert client.post("/api/v1/settings/ai/test").status_code == 400  # nicht eingerichtet
    configure(client)
    monkeypatch.setattr(ai, "_post", lambda *a: (200, {"choices": [{"message": {"content": "ok"}}]}))
    assert client.post("/api/v1/settings/ai/test").json() == {"ok": True, "model": "m-1", "reply": "ok"}
    monkeypatch.setattr(ai, "_post", lambda *a: (401, {}))
    r = client.post("/api/v1/settings/ai/test")
    assert r.status_code == 401 and r.json()["detail"]["error"] == "invalid_key"
    monkeypatch.setattr(ai, "_post", lambda *a: (500, {}))
    assert client.post("/api/v1/settings/ai/test").status_code == 502


# ------------------------------------------------------------------ Status, Warteschlange, Löschen


def run_queue(client, llm):
    """Arbeitet die Warteschlange synchron ab (ohne Hintergrund-Thread)."""
    st, q = client.app.state.store, client.app.state.ai_queue
    while (job := q.pop()):
        ai.process(st, job, call=llm)


def test_status_transitions(client, store):
    pk = add_ride(store)
    url = f"/api/v1/ai/ride/{pk}"
    assert client.get(url).json()["status"] == "off"
    configure(client)
    client.app.state.ai_queue.clear()
    r = client.get(url).json()
    assert r["status"] == "pending" and r["content"] is None and client.app.state.ai_queue.size() == 1
    client.get(url)
    assert client.app.state.ai_queue.size() == 1  # keine Doppelten
    llm = FakeLLM()
    run_queue(client, llm)
    r = client.get(url).json()
    assert r["status"] == "ready" and r["model"] == "m-1" and r["error"] is None and r["created_at"]
    assert r["content"]["summary"] == "Eine schöne Runde." and r["content"]["compare"] == "Etwas länger." and len(r["content"]["tips"]) == 4
    assert len(llm.calls) == 1
    client.put("/api/v1/settings/ai", json={"model": "m-2"})  # Modellwechsel -> veraltet
    r = client.get(url).json()
    assert r["status"] == "stale" and r["content"]["summary"] and r["model"] == "m-1"
    run_queue(client, llm)
    assert client.get(url).json()["status"] == "ready" and client.get(url).json()["model"] == "m-2"
    assert client.get(url + "?lang=en").json()["status"] == "pending"  # andere Sprache wird eingereiht


def test_error_status_keeps_old_text_and_fill_retries(client, store):
    pk = add_ride(store)
    configure(client)
    client.get(f"/api/v1/ai/ride/{pk}")
    run_queue(client, FakeLLM(ai.AiError("rate_limited", "Zu viele Anfragen")))
    r = client.get(f"/api/v1/ai/ride/{pk}").json()
    assert r["status"] == "error" and "Zu viele" in r["error"] and client.app.state.ai_queue.size() == 0
    assert client.get("/api/v1/settings/ai").json()["counts"]["errors"] == 1
    assert client.get("/api/v1/settings/ai").json()["last_error"] == "Zu viele Anfragen"
    assert client.post("/api/v1/settings/ai/fill").json()["queued"] >= 1
    run_queue(client, FakeLLM())
    assert client.get(f"/api/v1/ai/ride/{pk}").json()["status"] == "ready"
    assert client.get("/api/v1/settings/ai").json()["last_error"] is None
    bad = ai.process(store, ("ride", str(pk), "de"), call=FakeLLM("kein json"))
    assert bad == "skipped"  # Hash gleich, kein Fehler -> nichts zu tun


def test_bad_json_answer_is_stored_as_error(client, store):
    pk = add_ride(store)
    configure(client)
    assert ai.process(store, ("ride", str(pk), "de"), call=FakeLLM("Entschuldigung, das kann ich nicht.")) == "error"
    assert store.ai_text("ride", str(pk), "de")["error"]


def test_fatal_error_is_raised(client, store):
    pk = add_ride(store)
    configure(client)
    with pytest.raises(ai.AiError):
        ai.process(store, ("ride", str(pk), "de"), call=FakeLLM(ai.AiError("invalid_key", "abgelehnt")))
    assert store.ai_text("ride", str(pk), "de")["error"] == "abgelehnt"


def test_none_for_short_ride_and_empty_period(client, store):
    store.add_file("ride_rec/4242/ride_rec_7.proto", ride_bytes(n=100, ride_id=7))  # 0,5 km
    pk = store.list_rides()[0]["id"]
    configure(client)
    client.app.state.ai_queue.clear()
    assert client.get(f"/api/v1/ai/ride/{pk}").json()["status"] == "none"
    assert client.app.state.ai_queue.size() == 0
    llm = FakeLLM()
    assert ai.process(store, ("ride", str(pk), "de"), call=llm) == "skipped" and not llm.calls
    assert client.get("/api/v1/ai/ride/9999").status_code == 404


def test_dashboard_period_status_and_none(client, store):
    configure(client)
    assert client.get("/api/v1/ai/dashboard?period=month").json()["status"] == "none"  # keine Fahrten
    add_ride(store)
    client.app.state.ai_queue.clear()
    r = client.get("/api/v1/ai/dashboard?period=all").json()
    assert r["status"] == "pending"
    run_queue(client, FakeLLM())
    r = client.get("/api/v1/ai/dashboard?period=all").json()
    assert r["status"] == "ready" and r["content"]["trend"] == "y" and "compare" not in r["content"]
    assert client.get("/api/v1/ai/dashboard?period=day").status_code == 422


def test_fill_queues_rides_and_periods(client, store):
    pk = add_ride(store)
    configure(client)
    client.app.state.ai_queue.clear()
    n = client.post("/api/v1/settings/ai/fill").json()["queued"]
    assert n == 5  # Fahrt, Woche, Monat, Jahr, Gesamt
    assert client.post("/api/v1/settings/ai/fill").json()["queued"] == 0  # schon in der Schlange
    run_queue(client, FakeLLM())
    assert client.get("/api/v1/settings/ai").json()["counts"] == {"done": 5, "errors": 0, "pending": 0}
    assert client.post("/api/v1/settings/ai/fill").json()["queued"] == 0
    for ref in ai.ride_period_refs(store, pk):
        assert store.ai_text("period", ref, "de")["content"]


def test_new_ride_scan_enqueues(client, store):
    configure(client)
    client.app.state.ai_queue.clear()
    add_ride(store)
    assert ai.enqueue_new(store, client.app.state.ai_queue) == 5
    assert ai.enqueue_new(store, client.app.state.ai_queue) == 0


def test_delete_ride_removes_texts(client, store):
    pk = add_ride(store)
    pk2 = add_ride(store, ride_id=2, start=T0 + 10_000)
    configure(client)
    for p in (pk, pk2):
        ai.process(store, ("ride", str(p), "de"), call=FakeLLM())
        ai.process(store, ("ride", str(p), "en"), call=FakeLLM())
    assert client.delete(f"/api/v1/rides/{pk}").status_code == 200
    assert store.ai_text("ride", str(pk), "de") is None and store.ai_text("ride", str(pk), "en") is None
    assert store.ai_text("ride", str(pk2), "de")["content"]


def test_worker_thread_processes_queue(client, store):
    pk = add_ride(store)
    client.app.state.ai_call = FakeLLM()
    configure(client)
    client.post("/api/v1/settings/ai/fill")
    client.app.state.ai_paused = False
    client.app.state.ai_queue.wake.set()
    for _ in range(100):
        if not client.app.state.ai_queue.busy() and store.ai_counts()["done"] == 5:
            break
        time.sleep(0.05)
    assert store.ai_counts()["done"] == 5
    assert client.get(f"/api/v1/ai/ride/{pk}").json()["status"] == "ready"


def test_upload_wakes_scan(client, store):
    configure(client)
    client.app.state.ai_call = FakeLLM()
    client.app.state.ai_paused = False
    r = client.post("/api/v1/files", files={"files": ("ride_rec/4242/ride_rec_3.proto", ride_bytes(ride_id=3))})
    assert r.status_code == 200
    for _ in range(100):
        if store.ai_counts()["done"] == 5:
            break
        time.sleep(0.05)
    assert store.ai_counts()["done"] == 5


# ------------------------------------------------------------ Sicherheit

def test_settings_writes_admin_only_with_login(tmp_path, monkeypatch):
    for k in ("AVINOX_WEB_USER", "AVINOX_WEB_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai, "_post", lambda *a, **k: pytest.fail("echtes Netz"))
    settings = load_settings(tmp_path / "data")
    settings.web_user, settings.web_password = "admin", "admin-passwort-1"
    app = create_app(settings)
    app.state.ai_paused = True
    with TestClient(app) as c:
        app.state.auth.create_user("anna", "anna-passwort")  # kein Admin
        anna, admin = ("anna", "anna-passwort"), ("admin", "admin-passwort-1")
        assert c.get("/api/v1/settings/ai", auth=anna).status_code == 200
        assert c.put("/api/v1/settings/ai", json={"base_url": "https://evil.example/v1"}, auth=anna).status_code == 403
        assert c.post("/api/v1/settings/ai/test", auth=anna).status_code == 403
        assert c.post("/api/v1/settings/ai/fill", auth=anna).status_code == 403
        assert c.put("/api/v1/settings/ai", json={"enabled": True, "api_key": KEY}, auth=admin).status_code == 200


def test_key_cleared_when_host_changes(client):
    configure(client)
    d = client.put("/api/v1/settings/ai", json={"base_url": "https://openrouter.ai/api/v2"}).json()
    assert d["has_key"]  # gleicher Host: Schlüssel bleibt
    d = client.put("/api/v1/settings/ai", json={"base_url": "https://other.example/v1"}).json()
    assert not d["has_key"]  # neuer Host ohne neuen Schlüssel: gelöscht
    d = client.put("/api/v1/settings/ai", json={"base_url": "https://third.example/v1", "api_key": "sk-neu-9876"}).json()
    assert d["has_key"] and d["key_hint"].endswith("9876")
    d = client.put("/api/v1/settings/ai", json={"provider": "mistral"}).json()
    assert not d["has_key"]  # Anbieterwechsel setzt neue Adresse → Schlüssel weg


@pytest.mark.parametrize("url", ["https://x.example/v1?a=1", "https://x.example/v1#f", "https://user:pw@x.example/v1"])
def test_base_url_rejects_query_fragment_userinfo(client, url):
    assert client.put("/api/v1/settings/ai", json={"base_url": url}).status_code == 400
