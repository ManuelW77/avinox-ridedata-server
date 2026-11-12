"""Zweisprachige Oberfläche (Deutsch/Englisch) ohne Fremdabhängigkeit.

``t(key, lang)`` liest aus ``TEXTS``. Schlüssel mit Präfix ``js.`` landen als JSON in der Seite
(``js_texts``) und werden von den Skripten in ``static/`` gelesen. Schlüssel, die auf ``_html``
enden, enthalten vertrauenswürdiges HTML und kommen als ``Markup`` zurück.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime

from markupsafe import Markup

LANGS = ("de", "en")
DEFAULT_LANG = "de"
COOKIE = "lang"
COOKIE_MAX_AGE = 365 * 24 * 3600

_current: ContextVar[str] = ContextVar("avinox_lang", default=DEFAULT_LANG)
"""Sprache der gerade gerenderten Seite (Template-Filter und -Funktionen lesen sie hier)."""


def use(lang: str):
    """Setzt die aktuelle Sprache; Rückgabe ist das Token für ``reset``."""
    return _current.set(norm(lang))


def reset(token) -> None:
    _current.reset(token)


def current() -> str:
    return _current.get()

_DE: dict[str, str] = {
    # --- Rahmen
    "nav.overview": "Übersicht", "nav.rides": "Fahrten", "nav.bike": "Bike & Akku", "nav.sync": "Sync", "nav.settings": "Einstellungen",
    "lang.label": "Sprache", "footer.heart": "Herz", "footer.bike": "Fahrrad",
    # --- gemeinsam
    "btn.upload": "Dateien hochladen",
    "empty.none": "Noch keine Fahrten.",
    "empty.hint_html": 'Dateien vom Display unter <a href="/settings#upload">Einstellungen</a> hochladen oder mit <code>avinox import &lt;laufwerk&gt;</code> einlesen.',
    "no_data": "Keine Daten.",
    "no_time": "ohne Uhrzeit", "no_date": "ohne Datum",
    "ride_n": "Fahrt {n}",
    "ride_no_time": "Fahrt {n} (ohne Uhrzeit)",
    "time_suffix": " Uhr",
    "unit.km_per_pct": "km je 1 % Akku",
    # --- Übersicht
    "dash.title": "Übersicht",
    "dash.count": "{n} Fahrten", "dash.since": " seit {d}", "dash.last": " · zuletzt am {d}",
    "dash.period": "Zeitraum", "dash.prev": "Früherer Zeitraum", "dash.next": "Späterer Zeitraum",
    "period.day": "Tag", "period.week": "Woche", "period.month": "Monat", "period.year": "Jahr",
    "chart.distance": "Strecke", "chart.energy": "Energie", "chart.share": "Motoranteil",
    "chart.share_sub": "Anteil an der Gesamtenergie", "chart.ascent": "Höhenmeter",
    "legend.rider": "Fahrer", "legend.motor": "Motor",
    "jump.overview": "Übersicht", "js.jump.label": "Schnellsprung zu Abschnitten",
    "dash.mix.title": "Verteilung im Zeitraum",
    "ride.sec.course": "Strecke & Verlauf", "ride.sec.drive": "Antrieb", "ride.sec.power": "Leistung & Tempo", "ride.sec.hill": "Am Berg", "ride.sec.hill_sub": "↑ bergauf · ↓ bergab, nur in Bewegung", "ride.sec.stats": "Kennzahlen", "ride.sec.display": "Vom Display",
    "ride.grp.ride": "Strecke & Zeit", "ride.grp.rider": "Fahrer", "ride.grp.motor": "Motor & Akku",
    "dash.sec.recent": "Fahrten & Bilanz",
    "js.mix.sec.drive": "Antrieb", "js.mix.sec.power": "Leistung & Tempo", "js.mix.sec.hill": "Am Berg",
    "js.mix.modes": "Unterstützungsmodi", "js.mix.th.mode": "Modus", "js.mix.th.distance": "Strecke", "js.mix.th.avg_kmh": "Ø km/h", "js.mix.th.avg_motor": "Ø Motor", "js.mix.th.avg_rider": "Ø Fahrer",
    "js.mix.changes": "{n} Wechsel.", "js.mix.gears": "Gänge", "js.mix.gear_changes": "{n} Schaltvorgänge.",
    "js.mix.d.rider": "Fahrerleistung", "js.mix.d.motor": "Motorleistung", "js.mix.d.cadence": "Trittfrequenz", "js.mix.d.speed": "Tempo", "js.mix.d.gradient": "Steigung", "js.mix.d.flat": "flach", "js.mix.d.grad_hint": "↑ bergauf · ↓ bergab", "js.mix.d.power_grad": "Leistung nach Steigung", "js.mix.d.power_grad_sub": "Ø W, in Bewegung", "js.mix.d.legend_rider": "Fahrer", "js.mix.d.legend_motor": "Motor", "js.mix.d.speed_grad": "Tempo nach Steigung", "js.mix.d.speed_grad_sub": "Ø km/h, in Bewegung", "js.mix.d.cad_gear": "Trittfrequenz je Gang", "js.mix.d.cad_gear_sub": "Ø U/min beim Treten", "js.mix.d.rpm": "U/min", "js.mix.d.hr": "Puls",
    "js.mix.cadence_moving": "U/min, in Bewegung", "js.mix.speed_moving": "km/h, in Bewegung",
    "js.mix.in_motion": "W, in Bewegung", "js.mix.cadence_unit": "U/min", "js.mix.no_data": "Keine Daten.",
    "dash.hint": "Auf einen Balken tippen, um diesen Zeitraum anzusehen.",
    "bests.title": "Bestwerte", "bests.note": "Fahrten unter 1 km zählen nicht für Bestwerte.",
    "group.rider": "Deine Leistung", "group.motor": "Motor", "group.ride": "Fahrten & Akku", "group.period": "Beste Zeiträume",
    "best.to_ride": "Zur Fahrt", "best.to_day": "Zum Tag", "best.to_week": "Zur Woche", "best.to_month": "Zum Monat",
    "recent.title": "Letzte Fahrten", "recent.all": "Alle Fahrten", "recent.share_title": "Motoranteil {v} %",
    "total.title": "Gesamtbilanz",
    "fig.rides": "Fahrten", "fig.distance": "Strecke", "fig.time": "Fahrzeit", "fig.ascent": "Höhenmeter",
    "fig.rider_energy": "Eigene Energie", "fig.motor_energy": "Motorenergie", "fig.avg_share": "Ø Motoranteil", "fig.calories": "Kalorien",
    "dash.undated_one": "{n} Fahrt ohne Uhrzeit ist nur hier enthalten, nicht in den Zeiträumen.",
    "dash.undated_many": "{n} Fahrten ohne Uhrzeit sind nur hier enthalten, nicht in den Zeiträumen.",
    # --- Zeiträume (Python)
    "cur.day": "Heute", "cur.week": "Diese Woche", "cur.month": "Dieser Monat", "cur.year": "Dieses Jahr",
    "last.day": "Gestern", "last.week": "Letzte Woche", "last.month": "Letzter Monat", "last.year": "Letztes Jahr",
    "versus.day": "ggü. Vortag", "versus.week": "ggü. Vorwoche", "versus.month": "ggü. Vormonat", "versus.year": "ggü. Vorjahr",
    "period.before": "Zeitraum davor",
    "week.short": "KW {w}", "week.title": "KW {w}/{y} ({a}–{b})",
    "when.on": "Am {d}", "when.ride": "Bei Fahrt {n} (ohne Datum)", "when.week": "In KW {w}/{y}", "when.month": "Im {m} {y}",
    "bestp.title.day": "Bester Tag", "bestp.title.week": "Beste Woche", "bestp.title.month": "Bester Monat",
    "bestp.title.rider_kj": "{t} · Eigene Energie", "bestp.title.distance_km": "{t} · Strecke",
    "bestp.noun.day": "an einem Tag", "bestp.noun.week": "in einer Woche", "bestp.noun.month": "in einem Monat",
    "bestp.what.rider_kj": "am meisten selbst geleistet", "bestp.what.distance_km": "die längste Strecke geschafft",
    "bestp.rides": "bei {n} Fahrten", "js.prev_value": "vorher {v}",
    "bestp.sentence": "{when} hast du {noun} {what}: {v} bei {n} Fahrten.",
    # --- Bestwerte (Python)
    "best.rider_kj.title": "Meiste eigene Energie", "best.rider_kj.s": "{when} hast du die meiste eigene Energie aufgebracht: {v}.",
    "best.avg_rider_power.title": "Höchste Ø-Leistung", "best.avg_rider_power.s": "{when} bist du mit der höchsten Durchschnittsleistung gefahren: {v}.",
    "best.best_5min.title": "Stärkste 5 Minuten", "best.best_5min.s": "{when} hast du deine stärksten 5 Minuten getreten: {v} im Schnitt.",
    "best.best_20min.title": "Stärkste 20 Minuten", "best.best_20min.s": "{when} hast du deine stärksten 20 Minuten getreten: {v} im Schnitt.",
    "best.max_rider_power.title": "Höchste Spitzenleistung", "best.max_rider_power.s": "{when} hast du am kräftigsten in die Pedale getreten: {v} Spitze.",
    "best.motor_share_max.title": "Höchster Motoranteil", "best.motor_share_max.s": "{when} hat der Motor am meisten geholfen: {v} der Energie kamen von ihm.",
    "best.motor_share_min.title": "Niedrigster Motoranteil", "best.motor_share_min.s": "{when} hast du am meisten selbst getreten: nur {v} Motoranteil.",
    "best.motor_wh.title": "Meiste Motorenergie", "best.motor_wh.s": "{when} hat der Motor am meisten Energie geliefert: {v}.",
    "best.max_motor_power.title": "Höchste Motorleistung", "best.max_motor_power.s": "{when} hat der Motor am kräftigsten geschoben: {v} Spitze.",
    "best.distance.title": "Längste Fahrt", "best.distance.s": "{when} bist du am weitesten gefahren: {v}.",
    "best.ascent.title": "Meiste Höhenmeter", "best.ascent.s": "{when} hast du die meisten Höhenmeter gesammelt: {v}.",
    "best.max_speed.title": "Höchstes Tempo", "best.max_speed.s": "{when} warst du am schnellsten unterwegs: {v}.",
    "best.km_per_pct.title": "Sparsamste Fahrt", "best.km_per_pct.s": "{when} bist du am sparsamsten gefahren: {v}.",
    "headline": "{name}: {km} km", "headline.delta": ", {sign}{p} % {versus}",
    # --- Fahrtenliste
    "rides.title": "Fahrten",
    "rides.summary": "{n} Fahrten · {km} km · {hm} Hm · {h} h in Bewegung",
    "rides.per_month": "Pro Monat", "rides.month_label": "{m}/{y} · {n} Fahrten",
    "col.date": "Datum", "col.distance": "Strecke", "col.duration": "Dauer", "col.height": "Höhe", "col.avg_rider": "Ø Fahrer", "col.battery": "Akku",
    "gps.with": "mit GPS", "gps.without": "ohne GPS",
    # --- Bike & Akku
    "dev.title": "Bike & Akku", "dev.sub": "Stand aus der zuletzt hochgeladenen Nutzungsstatistik des Displays.",
    "fig.cycles": "Ladezyklen", "fig.boots": "Einschaltvorgänge", "fig.files": "gespeicherte Dateien", "fig.data": "Datenmenge",
    "fw.title": "Firmware", "fw.package": "Gesamtpaket", "fw.screen": "Display", "fw.motor": "Motor", "fw.ble": "Bluetooth", "fw.bat": "Akku",
    "dev.nostats": "Noch keine Nutzungsstatistik hochgeladen.",
    "cell.title": "Zellspannungsdifferenz",
    "cell.text": "Größte Differenz zwischen stärkster und schwächster Zelle je Einschaltvorgang. Sie ist der wichtigste Gesundheitswert des Akkus: eine größer werdende Differenz heißt, die Zellen driften auseinander.",
    "cell.shown": "Gezeigt: die neuesten 30 von {n}.",
    "hist.title": "Akku-Verlauf", "hist.end": "Ende", "hist.start_pct": "Start %", "hist.end_pct": "Ende %",
    "hist.min_cell": "min. Zelle mV", "hist.max_cell": "max. Zelle mV", "hist.delta": "Differenz mV",
    "hist.max_current": "max. Strom A", "hist.max_temp": "max. Temp. °C",
    "hms.title": "HMS-Meldungen", "hms.text": "Fehler- und Statusmeldungen des Antriebs. Die Bedeutung der Codes ist bisher unbekannt.",
    "hms.time": "Zeit", "hms.code": "Code", "hms.none": "Keine HMS-Meldungen hochgeladen.",
    # --- Fahrt
    "ride.sub": "Fahrt {n}", "ride.warnings": "{n} Hinweis(e)", "ride.raw": "Rohdatei", "ride.file_prefix": "fahrt",
    "ride.f.distance": "Strecke", "ride.f.duration": "Dauer", "ride.f.moving": "in Bewegung", "ride.f.ascent": "Höhengewinn", "ride.f.descent": "Höhenabnahme",
    "ride.f.avg_speed": "Ø Tempo", "ride.f.max_speed": "max. Tempo", "ride.f.avg_rider": "Ø Fahrerleistung", "ride.f.avg_pedaling": "Ø beim Treten", "ride.f.max_rider": "max. Fahrerleistung",
    "ride.f.avg_motor": "Ø Motorleistung", "ride.f.avg_motor_assist": "Ø bei Unterstützung", "ride.f.max_motor": "max. Motorleistung", "ride.f.cadence": "Ø Trittfrequenz", "ride.f.torque": "max. Drehmoment Fahrer",
    "ride.f.share": "Motoranteil", "ride.f.calories": "Kalorien", "ride.f.battery": "Akku", "ride.f.hr": "Ø Puls",
    "ride.f.cadence_unit": "U/min",
    "ride.track": "Strecke", "ride.color_by": "Linie einfärben nach",
    "ride.c.speed": "Tempo", "ride.c.rider": "Fahrer", "ride.c.motor": "Motor", "ride.c.alt": "Höhe", "ride.c.mode": "Modus",
    "ride.nogps_preview": "Ohne GPS aufgezeichnet – gezeigt wird nur die grobe Vorschau aus dem Display.",
    "ride.nogps": "Diese Fahrt hat keine GPS-Daten.",
    "ride.course": "Verlauf", "ride.xaxis": "x-Achse", "ride.x.distance": "Strecke", "ride.x.time": "Zeit",
    "ride.modes": "Unterstützungsmodi", "ride.th.mode": "Modus", "ride.th.distance": "Strecke", "ride.th.avg_kmh": "Ø km/h", "ride.th.avg_motor": "Ø Motor", "ride.th.avg_rider": "Ø Fahrer",
    "ride.changes": "{n} Wechsel.",
    "ride.custom_modes_html": ' Eigene Modi lassen sich unter <a href="/settings">Einstellungen</a> benennen.',
    "ride.gears": "Gänge", "ride.gear_changes": "{n} Schaltvorgänge.",
    "ride.d.rider": "Fahrerleistung", "ride.d.motor": "Motorleistung", "ride.d.cadence": "Trittfrequenz", "ride.d.speed": "Tempo", "ride.d.gradient": "Steigung", "ride.d.hr": "Puls",
    "ride.d.power_grad": "Leistung nach Steigung", "ride.d.power_grad_sub": "Ø W, in Bewegung", "ride.d.legend_rider": "Fahrer", "ride.d.legend_motor": "Motor",
    "ride.d.flat": "flach", "ride.d.grad_hint": "↑ bergauf · ↓ bergab", "ride.d.speed_grad": "Tempo nach Steigung", "ride.d.speed_grad_sub": "Ø km/h, in Bewegung",
    "ride.d.cad_gear": "Trittfrequenz je Gang", "ride.d.cad_gear_sub": "Ø U/min beim Treten", "ride.d.rpm": "U/min",
    "ride.in_motion": "W, in Bewegung", "ride.while_moving": "in Bewegung", "js.mix.while_moving": "in Bewegung",
    "ride.cadence_moving": "U/min, in Bewegung", "ride.speed_moving": "km/h, in Bewegung",
    "ride.efforts": "Bestleistungen", "ride.th.duration": "Dauer", "ride.th.rider": "Fahrer", "ride.th.motor": "Motor", "ride.th.speed": "Tempo",
    "ride.energy": "Energie & Akku", "ride.e.rider": "Fahrer", "ride.e.motor": "Motor", "ride.e.used": "Akku verbraucht", "ride.e.per_km": "je km",
    "ride.e.motor_wh": "{wh} Wh Motor", "ride.e.km_per_pct": "km je 1 %", "ride.e.per_100m": "je 100 Hm",
    "ride.env": "Umgebung & Höhe", "ride.v.temp": "Temperatur", "ride.v.pressure": "Luftdruck", "ride.v.alt": "Höhe", "ride.v.alt_sensor": "Hm aus Sensor",
    "ride.v.gradient": "Steigung", "ride.v.gps": "GPS",
    "ride.gradient_val": "Ø {avg} % bergauf, max. {max} %", "ride.avg_paren": "(Ø {v})", "ride.gps_val": "{n} Punkte, {p} % der Fahrt",
    "ride.display": "Vom Display", "ride.cells": "Akku-Zellen", "ride.cells_val": "{a} – {b} mV, Differenz max. {c} mV",
    "ride.peak_current": "Spitzenstrom", "ride.batt_temp": "Akkutemperatur", "ride.batt_temp_val": "{a} → {b} °C (max. {c})",
    "ride.boost": "Boost", "ride.throttle": "Drosselung (Hitze)", "ride.yes": "ja", "ride.no": "nein",
    "ride.fw_val": "Motor {m}, Display {s}, Akku {b}",
    "ride.pitch": "Strecke je Neigung (°)", "ride.torque_dist": "Motordrehmoment (Nm)",
    "ride.session_note": "Akku- und Neigungswerte gelten für den ganzen Einschaltvorgang ({n} Fahrten).",
    "ride.nostats_html": "Zu dieser Fahrt gibt es keine Nutzungsstatistik (<code>buried_rec</code>).",
    # --- Einstellungen
    "set.title": "Einstellungen",
    "set.lang": "Sprache",
    "set.lang_text": "Sprache der Weboberfläche. Wird im Browser gespeichert.",
    "set.modes": "Eigene Modi benennen",
    "set.modes_text_html": "Die Nummer eines eigenen Modus findest du mit einer kurzen Testfahrt im jeweiligen Modus heraus: Sie steht danach in der Fahrt unter „Unterstützungsmodi“ als <code>eigen_&lt;nr&gt;</code>. Leeres Feld = Name entfernen.",
    "set.mode_n": "Modus {n}", "set.save": "Speichern",
    "set.nomodes": "In den Fahrten kamen bisher noch keine eigenen Modi vor.",
    "set.upload": "Dateien hochladen",
    "set.upload_text_html": "Wähle das USB-Laufwerk des Displays (oder eine Kopie davon) aus. Hochgeladen werden nur Fahrten, Nutzungsstatistik und HMS-Meldungen – die großen <code>ebike_log_*.dat</code> werden nie gelesen oder gesendet. Bereits bekannte Dateien werden erkannt.",
    "set.accepted": "Diese Dateien werden übernommen:", "set.rejected": "Nicht übernommen:",
    "upload.kind.ride": "Fahrtaufzeichnungen", "upload.kind.index": "Fahrten-Index",
    "upload.kind.buried": "Nutzungsstatistik (Akku, Gänge, Modi, Firmware)", "upload.kind.hms": "Meldungen des Antriebs",
    "notup.1": "verschlüsselte Systemlogs, zusammen ca. 3 GB", "notup.2": "Index und leere Kartenvorschau", "notup.3": "alle übrigen Dateien und Ordner",
    "notup.path3": "alles andere",
    "set.esp_same": "Der ESP32 lädt dieselben Dateien hoch.",
    "set.drop": "Ordner hierher ziehen oder", "set.choose": "Ordner auswählen", "set.to_rides": "Zur Fahrtenliste",
    "set.fw": "Firmware für den ESP32",
    "set.fw_text_html": "Der ESP32 fragt hier nach neuen Versionen und aktualisiert sich selbst (OTA). Image: <code>esp32/avinox-sync/build/avinox-sync.bin</code>.",
    "set.fw_current": "Aktuell", "set.fw_uploaded": "Hochgeladen", "set.fw_size": "Größe", "set.fw_none": "Noch keine Firmware hochgeladen.",
    "set.fw_admin_hint": "Firmware-Verwaltung nur mit Web-Passwort (AVINOX_WEB_USER/AVINOX_WEB_PASSWORD) – oder per ‚avinox firmware add‘ auf dem Server",
    "set.fw_older": "Ältere Versionen", "set.fw_make_current": "als aktuell setzen", "set.fw_delete": "löschen", "set.fw_upload_btn": "Hochladen",
    "set.api": "ESP32 / Upload-API", "set.server_url": "Server-URL für den ESP32", "set.copy": "kopieren",
    "set.url_cfg": "Eingestellt (AVINOX_PUBLIC_URL)", "set.url_called": "Aufgerufene Adresse", "set.url_lan": "LAN-IP (nur im Heimnetz)",
    "set.url_https_hint": "empfohlen, funktioniert auch unterwegs",
    "set.url_explain": "Wähle eine Adresse: Eine HTTPS-Adresse funktioniert überall, eine IP-Adresse nur im eigenen WLAN, dafür ohne Proxy und DNS.",
    "set.url_docker_html": "Im Docker-Container ist die LAN-IP nicht erkennbar – <code>AVINOX_PUBLIC_URL</code> z. B. auf <code>http://&lt;Server-IP&gt;:8000</code> setzen.",
    "set.url_unreachable_html": "Diese Adresse erreicht der ESP32 nicht – öffne die Seite über die IP oder den Namen des Servers im WLAN oder setze <code>AVINOX_PUBLIC_URL</code>.",
    "set.token": "Upload-Token", "set.show": "anzeigen",
    "set.noauth_html": "Die Seite ist ohne Passwort im WLAN offen – für den Dauerbetrieb <code>AVINOX_WEB_USER</code>/<code>AVINOX_WEB_PASSWORD</code> setzen.",
    "set.bearer_html": "Alle Aufrufe mit Header <code>Authorization: Bearer &lt;Token&gt;</code>.",
    "set.api.manifest": "Liste der bekannten Dateien (Pfad + Größe) – daran erkennt der ESP32, was neu ist.",
    "set.api.upload_html": "Neue Datei als rohe Bytes, in Stücken und wiederaufnehmbar. Passt <code>offset</code> nicht, antwortet der Server mit 409 und dem Feld <code>received</code> (bisher empfangene Bytes).",
    "set.api.status": "Aktueller Stand eines Uploads.",
    "set.api.fw_latest": "Aktuelle Firmware (Version, Größe, SHA-256, URL) oder 404.", "set.api.fw_bin": "Firmware-Image.",
    "set.api.full": "Vollständige Beschreibung:",
    # --- Sync
    "sync.title": "Sync", "sync.sub": "Wann sich der ESP32 gemeldet hat, was er gesendet hat und ob es geklappt hat.",
    "sync.kind.manifest": "Abgleich", "sync.kind.upload_chunk_error": "Upload-Fehler", "sync.kind.file_new": "Datei neu", "sync.kind.file_known": "Datei bekannt",
    "sync.kind.auth_error": "Token abgelehnt", "sync.kind.report": "Sync-Bericht", "sync.kind.heartbeat": "Lebenszeichen",
    "sync.kind.firmware_check": "Firmware-Abfrage", "sync.kind.firmware_download": "Firmware-Download", "sync.kind.ota": "Firmware-Update",
    "sync.ago.now": "gerade eben", "sync.ago": "vor {span}", "sync.span.min": "{n} min", "sync.span.h": "{n} h", "sync.span.d": "{n} Tagen",
    "sync.empty": "Noch keine Einsendungen. Sobald sich der ESP32 meldet, erscheint er hier.",
    "sync.setup": "Einrichtung: Adresse und Upload-Token in den Einstellungen",
    "sync.offline_since": "offline seit {t}", "sync.online": "online",
    "sync.last_seen": "Zuletzt gesehen", "sync.ip": "IP", "sync.firmware": "Firmware", "sync.fw_available": " – verfügbar {v}", "sync.fw_current": " – aktuell",
    "sync.wifi": "WLAN-Signal", "sync.rssi.good": "gut", "sync.rssi.mid": "mittel", "sync.rssi.weak": "schwach",
    "sync.uptime": "Laufzeit", "sync.heap": "Freier Speicher", "sync.drive": "Laufwerk",
    "sync.drive.present": "erkannt", "sync.drive.absent": "nicht erkannt", "sync.drive.absent_hint": "Display an? Richtige (native) Buchse?", "sync.drive.error": "Fehler",
    "sync.last_sync": "Letzter Sync", "sync.time": "Zeit", "sync.result": "Ergebnis", "sync.res.ok": "OK", "sync.res.warn": "Warnung", "sync.res.error": "Fehler",
    "sync.files": "Dateien", "sync.files_val": "{new} neu, {skipped} übersprungen, {failed} fehlerhaft", "sync.files_of": " (von {n})",
    "sync.volume": "Datenmenge", "sync.duration": "Dauer", "sync.no_sync": "Noch kein Sync gemeldet.",
    "sync.silent": "Seit {span} kein Lebenszeichen (zuletzt {t}). Das Bike kann einfach aus sein.",
    "sync.events": "Letzte Ereignisse", "sync.th.time": "Zeit", "sync.th.ip": "IP", "sync.th.device": "Gerät", "sync.th.kind": "Art", "sync.th.file": "Datei",
    "sync.th.size": "Größe", "sync.th.detail": "Status / Detail",
    # --- JS (Präfix js.)
    "js.kpi.rides": "Fahrten", "js.kpi.moving": "Fahrzeit", "js.kpi.ascent": "Höhenmeter", "js.kpi.rider": "Eigene Energie", "js.kpi.avg_rider": "Ø Fahrerleistung",
    "js.kpi.motor_wh": "Motorenergie", "js.kpi.share": "Motoranteil", "js.kpi.battery": "Akkuverbrauch", "js.kpi.calories": "Kalorien",
    "js.no_compare": "kein Vergleich", "js.same": "wie davor", "js.pts": "Pkt.", "js.nothing_before": "davor nichts",
    "js.versus_prefix": "ggü. ",
    "js.hero_prev": "{versus}: {km} km", "js.hero_vs": "{delta} {versus} ({km} km)", "js.hero_none_before": "Im Zeitraum davor keine Fahrt.",
    "js.you": "Du", "js.motor": "Motor", "js.no_rides_period": "In diesem Zeitraum keine Fahrt.", "js.jump_last": "Zur letzten Fahrt springen",
    "js.span.day": "{n} Tagen", "js.span.week": "{n} Wochen", "js.span.month": "{n} Monaten", "js.span.year": "{n} Jahren",
    "js.sum_in": "{v} {unit} in {span}",
    "js.charts_failed": "Diagramme konnten nicht geladen werden.", "js.period": "Zeitraum",
    "js.s.distance": "Strecke", "js.s.rider": "Fahrer", "js.s.motor": "Motor", "js.s.share": "Motoranteil", "js.s.ascent": "Höhenmeter",
    "js.no_rides_chart": "Keine Fahrten in diesem Zeitraum.", "js.chart_unavailable": "Diagramm nicht verfügbar.",
    "js.mode_n": "Modus {n}", "js.no_values": "keine Werte", "js.preview_nogps": "Vorschau (ohne GPS)", "js.start": "Start", "js.end": "Ende",
    "js.no_course": "Keine Verlaufsdaten.", "js.time": "Zeit", "js.distance": "Strecke",
    "js.c.alt": "Höhe", "js.c.speed": "Tempo", "js.c.power": "Leistung", "js.c.cadence": "Trittfrequenz", "js.c.battery": "Akku", "js.c.temp": "Temperatur",
    "js.rpm": "U/min",
    "js.saving": "Speichere …", "js.saved": "Gespeichert.", "js.error_n": "Fehler {n}", "js.error_msg": "Fehler: {m}",
    "js.upload_progress": "{done} / {total} · neu: {new} · bekannt: {known} · Fehler: {err}",
    "js.upload_none": "Keine passenden Dateien gefunden (erwartet: Ordner mit ride_rec/, buried_rec/ oder hms_rec/).",
    "js.reading_folder": "Lese Ordner …", "js.hide": "verbergen", "js.show": "anzeigen", "js.copied": "kopiert", "js.copy": "kopieren",
    "js.fw_uploading": "Lade hoch …", "js.fw_confirm_delete": "Diese Firmware-Version löschen?",
}

_EN: dict[str, str] = {
    "nav.overview": "Overview", "nav.rides": "Rides", "nav.bike": "Bike & battery", "nav.sync": "Sync", "nav.settings": "Settings",
    "lang.label": "Language", "footer.heart": "heart", "footer.bike": "bike",
    "btn.upload": "Upload files",
    "empty.none": "No rides yet.",
    "empty.hint_html": 'Upload files from the display under <a href="/settings#upload">Settings</a> or import them with <code>avinox import &lt;drive&gt;</code>.',
    "no_data": "No data.",
    "no_time": "no time", "no_date": "no date",
    "ride_n": "Ride {n}",
    "ride_no_time": "Ride {n} (no time)",
    "time_suffix": "",
    "unit.km_per_pct": "km per 1 % battery",
    "dash.title": "Overview",
    "dash.count": "{n} rides", "dash.since": " since {d}", "dash.last": " · latest on {d}",
    "dash.period": "Period", "dash.prev": "Earlier period", "dash.next": "Later period",
    "period.day": "Day", "period.week": "Week", "period.month": "Month", "period.year": "Year",
    "chart.distance": "Distance", "chart.energy": "Energy", "chart.share": "Motor share",
    "chart.share_sub": "Share of total energy", "chart.ascent": "Elevation gain",
    "legend.rider": "Rider", "legend.motor": "Motor",
    "jump.overview": "Overview", "js.jump.label": "Jump to section",
    "dash.mix.title": "Distribution in this period",
    "ride.sec.course": "Route & course", "ride.sec.drive": "Drivetrain", "ride.sec.power": "Power & speed", "ride.sec.hill": "On climbs", "ride.sec.hill_sub": "↑ uphill · ↓ downhill, while moving", "ride.sec.stats": "Key figures", "ride.sec.display": "From the display",
    "ride.grp.ride": "Distance & time", "ride.grp.rider": "Rider", "ride.grp.motor": "Motor & battery",
    "dash.sec.recent": "Rides & totals",
    "js.mix.sec.drive": "Drivetrain", "js.mix.sec.power": "Power & speed", "js.mix.sec.hill": "On climbs",
    "js.mix.modes": "Assist modes", "js.mix.th.mode": "Mode", "js.mix.th.distance": "Distance", "js.mix.th.avg_kmh": "Avg. km/h", "js.mix.th.avg_motor": "Avg. motor", "js.mix.th.avg_rider": "Avg. rider",
    "js.mix.changes": "{n} changes.", "js.mix.gears": "Gears", "js.mix.gear_changes": "{n} gear changes.",
    "js.mix.d.rider": "Rider power", "js.mix.d.motor": "Motor power", "js.mix.d.cadence": "Cadence", "js.mix.d.speed": "Speed", "js.mix.d.gradient": "Gradient", "js.mix.d.flat": "flat", "js.mix.d.grad_hint": "↑ uphill · ↓ downhill", "js.mix.d.power_grad": "Power by gradient", "js.mix.d.power_grad_sub": "avg W, while moving", "js.mix.d.legend_rider": "Rider", "js.mix.d.legend_motor": "Motor", "js.mix.d.speed_grad": "Speed by gradient", "js.mix.d.speed_grad_sub": "avg km/h, while moving", "js.mix.d.cad_gear": "Cadence per gear", "js.mix.d.cad_gear_sub": "avg rpm while pedaling", "js.mix.d.rpm": "rpm", "js.mix.d.hr": "Heart rate",
    "js.mix.cadence_moving": "rpm, while moving", "js.mix.speed_moving": "km/h, while moving",
    "js.mix.in_motion": "W, while moving", "js.mix.cadence_unit": "rpm", "js.mix.no_data": "No data.",
    "dash.hint": "Tap a bar to view that period.",
    "bests.title": "Personal bests", "bests.note": "Rides under 1 km do not count towards personal bests.",
    "group.rider": "Your power", "group.motor": "Motor", "group.ride": "Rides & battery", "group.period": "Best periods",
    "best.to_ride": "View ride", "best.to_day": "View day", "best.to_week": "View week", "best.to_month": "View month",
    "recent.title": "Latest rides", "recent.all": "All rides", "recent.share_title": "Motor share {v} %",
    "total.title": "Totals",
    "fig.rides": "Rides", "fig.distance": "Distance", "fig.time": "Ride time", "fig.ascent": "Elevation gain",
    "fig.rider_energy": "Rider energy", "fig.motor_energy": "Motor energy", "fig.avg_share": "Avg. motor share", "fig.calories": "Calories",
    "dash.undated_one": "{n} ride without a time is only included here, not in the periods.",
    "dash.undated_many": "{n} rides without a time are only included here, not in the periods.",
    "cur.day": "Today", "cur.week": "This week", "cur.month": "This month", "cur.year": "This year",
    "last.day": "Yesterday", "last.week": "Last week", "last.month": "Last month", "last.year": "Last year",
    "versus.day": "vs. previous day", "versus.week": "vs. previous week", "versus.month": "vs. previous month", "versus.year": "vs. previous year",
    "period.before": "Previous period",
    "week.short": "CW {w}", "week.title": "CW {w}/{y} ({a}–{b})",
    "when.on": "On {d}", "when.ride": "On ride {n} (no date)", "when.week": "In CW {w}/{y}", "when.month": "In {m} {y}",
    "bestp.title.day": "Best day", "bestp.title.week": "Best week", "bestp.title.month": "Best month",
    "bestp.title.rider_kj": "{t} · Rider energy", "bestp.title.distance_km": "{t} · Distance",
    "bestp.noun.day": "in a day", "bestp.noun.week": "in a week", "bestp.noun.month": "in a month",
    "bestp.what.rider_kj": "contributed the most yourself", "bestp.what.distance_km": "covered the longest distance",
    "bestp.rides": "over {n} rides", "js.prev_value": "before: {v}",
    "bestp.sentence": "{when} you {what} {noun}: {v} over {n} rides.",
    "best.rider_kj.title": "Most rider energy", "best.rider_kj.s": "{when} you put out the most rider energy: {v}.",
    "best.avg_rider_power.title": "Highest avg. power", "best.avg_rider_power.s": "{when} you rode with the highest average power: {v}.",
    "best.best_5min.title": "Strongest 5 minutes", "best.best_5min.s": "{when} you pedalled your strongest 5 minutes: {v} on average.",
    "best.best_20min.title": "Strongest 20 minutes", "best.best_20min.s": "{when} you pedalled your strongest 20 minutes: {v} on average.",
    "best.max_rider_power.title": "Highest peak power", "best.max_rider_power.s": "{when} you pushed the hardest on the pedals: {v} peak.",
    "best.motor_share_max.title": "Highest motor share", "best.motor_share_max.s": "{when} the motor helped the most: {v} of the energy came from it.",
    "best.motor_share_min.title": "Lowest motor share", "best.motor_share_min.s": "{when} you pedalled the most yourself: only {v} motor share.",
    "best.motor_wh.title": "Most motor energy", "best.motor_wh.s": "{when} the motor delivered the most energy: {v}.",
    "best.max_motor_power.title": "Highest motor power", "best.max_motor_power.s": "{when} the motor pushed the hardest: {v} peak.",
    "best.distance.title": "Longest ride", "best.distance.s": "{when} you rode the farthest: {v}.",
    "best.ascent.title": "Most elevation gain", "best.ascent.s": "{when} you climbed the most: {v}.",
    "best.max_speed.title": "Top speed", "best.max_speed.s": "{when} you were the fastest: {v}.",
    "best.km_per_pct.title": "Most economical ride", "best.km_per_pct.s": "{when} you rode the most economically: {v}.",
    "headline": "{name}: {km} km", "headline.delta": ", {sign}{p} % {versus}",
    "rides.title": "Rides",
    "rides.summary": "{n} rides · {km} km · {hm} m ascent · {h} h moving",
    "rides.per_month": "Per month", "rides.month_label": "{m}/{y} · {n} rides",
    "col.date": "Date", "col.distance": "Distance", "col.duration": "Duration", "col.height": "Climb", "col.avg_rider": "Avg. rider", "col.battery": "Battery",
    "gps.with": "with GPS", "gps.without": "without GPS",
    "dev.title": "Bike & battery", "dev.sub": "Status from the most recently uploaded usage statistics of the display.",
    "fig.cycles": "Charge cycles", "fig.boots": "Power-ons", "fig.files": "stored files", "fig.data": "Data volume",
    "fw.title": "Firmware", "fw.package": "Overall package", "fw.screen": "Display", "fw.motor": "Motor", "fw.ble": "Bluetooth", "fw.bat": "Battery",
    "dev.nostats": "No usage statistics uploaded yet.",
    "cell.title": "Cell voltage difference",
    "cell.text": "Largest difference between the strongest and the weakest cell per power-on. It is the most important health indicator of the battery: a growing difference means the cells are drifting apart.",
    "cell.shown": "Showing the latest 30 of {n}.",
    "hist.title": "Battery history", "hist.end": "End", "hist.start_pct": "Start %", "hist.end_pct": "End %",
    "hist.min_cell": "min. cell mV", "hist.max_cell": "max. cell mV", "hist.delta": "Difference mV",
    "hist.max_current": "max. current A", "hist.max_temp": "max. temp. °C",
    "hms.title": "HMS messages", "hms.text": "Error and status messages of the drive unit. The meaning of the codes is still unknown.",
    "hms.time": "Time", "hms.code": "Code", "hms.none": "No HMS messages uploaded.",
    "ride.sub": "Ride {n}", "ride.warnings": "{n} warning(s)", "ride.raw": "Raw file", "ride.file_prefix": "ride",
    "ride.f.distance": "Distance", "ride.f.duration": "Duration", "ride.f.moving": "moving", "ride.f.ascent": "Elevation gain", "ride.f.descent": "Elevation loss",
    "ride.f.avg_speed": "Avg. speed", "ride.f.max_speed": "max. speed", "ride.f.avg_rider": "Avg. rider power", "ride.f.avg_pedaling": "Avg. while pedaling", "ride.f.max_rider": "max. rider power",
    "ride.f.avg_motor": "Avg. motor power", "ride.f.avg_motor_assist": "Avg. while assisting", "ride.f.max_motor": "max. motor power", "ride.f.cadence": "Avg. cadence", "ride.f.torque": "max. rider torque",
    "ride.f.share": "Motor share", "ride.f.calories": "Calories", "ride.f.battery": "Battery", "ride.f.hr": "Avg. heart rate",
    "ride.f.cadence_unit": "rpm",
    "ride.track": "Route", "ride.color_by": "Colour line by",
    "ride.c.speed": "Speed", "ride.c.rider": "Rider", "ride.c.motor": "Motor", "ride.c.alt": "Altitude", "ride.c.mode": "Mode",
    "ride.nogps_preview": "Recorded without GPS – only the rough preview from the display is shown.",
    "ride.nogps": "This ride has no GPS data.",
    "ride.course": "Profile", "ride.xaxis": "x-axis", "ride.x.distance": "Distance", "ride.x.time": "Time",
    "ride.modes": "Assist modes", "ride.th.mode": "Mode", "ride.th.distance": "Distance", "ride.th.avg_kmh": "Avg. km/h", "ride.th.avg_motor": "Avg. motor", "ride.th.avg_rider": "Avg. rider",
    "ride.changes": "{n} changes.",
    "ride.custom_modes_html": ' Custom modes can be named under <a href="/settings">Settings</a>.',
    "ride.gears": "Gears", "ride.gear_changes": "{n} gear changes.",
    "ride.d.rider": "Rider power", "ride.d.motor": "Motor power", "ride.d.cadence": "Cadence", "ride.d.speed": "Speed", "ride.d.gradient": "Gradient", "ride.d.hr": "Heart rate",
    "ride.d.power_grad": "Power by gradient", "ride.d.power_grad_sub": "avg W, while moving", "ride.d.legend_rider": "Rider", "ride.d.legend_motor": "Motor",
    "ride.d.flat": "flat", "ride.d.grad_hint": "↑ uphill · ↓ downhill", "ride.d.speed_grad": "Speed by gradient", "ride.d.speed_grad_sub": "avg km/h, while moving",
    "ride.d.cad_gear": "Cadence per gear", "ride.d.cad_gear_sub": "avg rpm while pedaling", "ride.d.rpm": "rpm",
    "ride.in_motion": "W, while moving", "ride.while_moving": "while moving", "js.mix.while_moving": "while moving",
    "ride.cadence_moving": "rpm, while moving", "ride.speed_moving": "km/h, while moving",
    "ride.efforts": "Best efforts", "ride.th.duration": "Duration", "ride.th.rider": "Rider", "ride.th.motor": "Motor", "ride.th.speed": "Speed",
    "ride.energy": "Energy & battery", "ride.e.rider": "Rider", "ride.e.motor": "Motor", "ride.e.used": "Battery used", "ride.e.per_km": "per km",
    "ride.e.motor_wh": "{wh} Wh motor", "ride.e.km_per_pct": "km per 1 %", "ride.e.per_100m": "per 100 m climb",
    "ride.env": "Environment & altitude", "ride.v.temp": "Temperature", "ride.v.pressure": "Air pressure", "ride.v.alt": "Altitude", "ride.v.alt_sensor": "Climb from sensor",
    "ride.v.gradient": "Gradient", "ride.v.gps": "GPS",
    "ride.gradient_val": "avg. {avg} % uphill, max. {max} %", "ride.avg_paren": "(avg. {v})", "ride.gps_val": "{n} points, {p} % of the ride",
    "ride.display": "From the display", "ride.cells": "Battery cells", "ride.cells_val": "{a} – {b} mV, difference max. {c} mV",
    "ride.peak_current": "Peak current", "ride.batt_temp": "Battery temperature", "ride.batt_temp_val": "{a} → {b} °C (max. {c})",
    "ride.boost": "Boost", "ride.throttle": "Throttling (heat)", "ride.yes": "yes", "ride.no": "no",
    "ride.fw_val": "Motor {m}, display {s}, battery {b}",
    "ride.pitch": "Distance per incline (°)", "ride.torque_dist": "Motor torque (Nm)",
    "ride.session_note": "Battery and incline values apply to the whole power-on session ({n} rides).",
    "ride.nostats_html": "There are no usage statistics for this ride (<code>buried_rec</code>).",
    "set.title": "Settings",
    "set.lang": "Language",
    "set.lang_text": "Language of the web interface. Stored in your browser.",
    "set.modes": "Name custom modes",
    "set.modes_text_html": "You can find the number of a custom mode with a short test ride in that mode: afterwards it appears in the ride under “Assist modes” as <code>eigen_&lt;nr&gt;</code>. Empty field = remove name.",
    "set.mode_n": "Mode {n}", "set.save": "Save",
    "set.nomodes": "No custom modes have appeared in your rides so far.",
    "set.upload": "Upload files",
    "set.upload_text_html": "Select the display's USB drive (or a copy of it). Only rides, usage statistics and HMS messages are uploaded – the large <code>ebike_log_*.dat</code> files are never read or sent. Already known files are recognised.",
    "set.accepted": "These files are accepted:", "set.rejected": "Not accepted:",
    "upload.kind.ride": "Ride recordings", "upload.kind.index": "Ride index",
    "upload.kind.buried": "Usage statistics (battery, gears, modes, firmware)", "upload.kind.hms": "Drive unit messages",
    "notup.1": "encrypted system logs, about 3 GB in total", "notup.2": "Index and empty map preview", "notup.3": "all other files and folders",
    "notup.path3": "everything else",
    "set.esp_same": "The ESP32 uploads the same files.",
    "set.drop": "Drag a folder here or", "set.choose": "Choose folder", "set.to_rides": "Go to the ride list",
    "set.fw": "Firmware for the ESP32",
    "set.fw_text_html": "The ESP32 checks here for new versions and updates itself (OTA). Image: <code>esp32/avinox-sync/build/avinox-sync.bin</code>.",
    "set.fw_current": "Current", "set.fw_uploaded": "Uploaded", "set.fw_size": "Size", "set.fw_none": "No firmware uploaded yet.",
    "set.fw_admin_hint": "Firmware management requires a web password (AVINOX_WEB_USER/AVINOX_WEB_PASSWORD) – or use ‘avinox firmware add’ on the server",
    "set.fw_older": "Older versions", "set.fw_make_current": "set as current", "set.fw_delete": "delete", "set.fw_upload_btn": "Upload",
    "set.api": "ESP32 / upload API", "set.server_url": "Server URL for the ESP32", "set.copy": "copy",
    "set.url_cfg": "Configured (AVINOX_PUBLIC_URL)", "set.url_called": "Address as opened", "set.url_lan": "LAN IP (home network only)",
    "set.url_https_hint": "recommended, also works on the road",
    "set.url_explain": "Pick one address: an HTTPS address works everywhere, an IP address only on your own Wi-Fi, but needs no proxy or DNS.",
    "set.url_docker_html": "Inside a Docker container the LAN IP cannot be detected – set <code>AVINOX_PUBLIC_URL</code> e.g. to <code>http://&lt;server-IP&gt;:8000</code>.",
    "set.url_unreachable_html": "The ESP32 cannot reach this address – open the page via the IP or name of the server on the Wi-Fi, or set <code>AVINOX_PUBLIC_URL</code>.",
    "set.token": "Upload token", "set.show": "show",
    "set.noauth_html": "The page is open without a password on the Wi-Fi – for permanent use set <code>AVINOX_WEB_USER</code>/<code>AVINOX_WEB_PASSWORD</code>.",
    "set.bearer_html": "All calls with header <code>Authorization: Bearer &lt;token&gt;</code>.",
    "set.api.manifest": "List of known files (path + size) – this is how the ESP32 knows what is new.",
    "set.api.upload_html": "New file as raw bytes, in chunks and resumable. If <code>offset</code> does not match, the server answers 409 with the field <code>received</code> (bytes received so far).",
    "set.api.status": "Current state of an upload.",
    "set.api.fw_latest": "Current firmware (version, size, SHA-256, URL) or 404.", "set.api.fw_bin": "Firmware image.",
    "set.api.full": "Full description:",
    "sync.title": "Sync", "sync.sub": "When the ESP32 checked in, what it sent and whether it worked.",
    "sync.kind.manifest": "File list check", "sync.kind.upload_chunk_error": "Upload error", "sync.kind.file_new": "New file", "sync.kind.file_known": "Known file",
    "sync.kind.auth_error": "Token rejected", "sync.kind.report": "Sync report", "sync.kind.heartbeat": "Heartbeat",
    "sync.kind.firmware_check": "Firmware check", "sync.kind.firmware_download": "Firmware download", "sync.kind.ota": "Firmware update",
    "sync.ago.now": "just now", "sync.ago": "{span} ago", "sync.span.min": "{n} min", "sync.span.h": "{n} h", "sync.span.d": "{n} days",
    "sync.empty": "No submissions yet. As soon as the ESP32 checks in, it will appear here.",
    "sync.setup": "Setup: address and upload token in the settings",
    "sync.offline_since": "offline since {t}", "sync.online": "online",
    "sync.last_seen": "Last seen", "sync.ip": "IP", "sync.firmware": "Firmware", "sync.fw_available": " – {v} available", "sync.fw_current": " – up to date",
    "sync.wifi": "Wi-Fi signal", "sync.rssi.good": "good", "sync.rssi.mid": "fair", "sync.rssi.weak": "weak",
    "sync.uptime": "Uptime", "sync.heap": "Free memory", "sync.drive": "Drive",
    "sync.drive.present": "detected", "sync.drive.absent": "not detected", "sync.drive.absent_hint": "Display on? Correct (native) port?", "sync.drive.error": "Error",
    "sync.last_sync": "Last sync", "sync.time": "Time", "sync.result": "Result", "sync.res.ok": "OK", "sync.res.warn": "Warning", "sync.res.error": "Error",
    "sync.files": "Files", "sync.files_val": "{new} new, {skipped} skipped, {failed} failed", "sync.files_of": " (of {n})",
    "sync.volume": "Data volume", "sync.duration": "Duration", "sync.no_sync": "No sync reported yet.",
    "sync.silent": "No sign of life for {span} (last seen {t}). The bike may simply be switched off.",
    "sync.events": "Latest events", "sync.th.time": "Time", "sync.th.ip": "IP", "sync.th.device": "Device", "sync.th.kind": "Type", "sync.th.file": "File",
    "sync.th.size": "Size", "sync.th.detail": "Status / detail",
    "js.kpi.rides": "Rides", "js.kpi.moving": "Ride time", "js.kpi.ascent": "Elevation gain", "js.kpi.rider": "Rider energy", "js.kpi.avg_rider": "Avg. rider power",
    "js.kpi.motor_wh": "Motor energy", "js.kpi.share": "Motor share", "js.kpi.battery": "Battery used", "js.kpi.calories": "Calories",
    "js.no_compare": "no comparison", "js.same": "same as before", "js.pts": "pts", "js.nothing_before": "nothing before",
    "js.versus_prefix": "vs. ",
    "js.hero_prev": "{versus}: {km} km", "js.hero_vs": "{delta} {versus} ({km} km)", "js.hero_none_before": "No ride in the previous period.",
    "js.you": "You", "js.motor": "Motor", "js.no_rides_period": "No rides in this period.", "js.jump_last": "Jump to the latest ride",
    "js.span.day": "{n} days", "js.span.week": "{n} weeks", "js.span.month": "{n} months", "js.span.year": "{n} years",
    "js.sum_in": "{v} {unit} in {span}",
    "js.charts_failed": "Charts could not be loaded.", "js.period": "Period",
    "js.s.distance": "Distance", "js.s.rider": "Rider", "js.s.motor": "Motor", "js.s.share": "Motor share", "js.s.ascent": "Elevation gain",
    "js.no_rides_chart": "No rides in this period.", "js.chart_unavailable": "Chart not available.",
    "js.mode_n": "Mode {n}", "js.no_values": "no values", "js.preview_nogps": "Preview (no GPS)", "js.start": "Start", "js.end": "End",
    "js.no_course": "No profile data.", "js.time": "Time", "js.distance": "Distance",
    "js.c.alt": "Altitude", "js.c.speed": "Speed", "js.c.power": "Power", "js.c.cadence": "Cadence", "js.c.battery": "Battery", "js.c.temp": "Temperature",
    "js.rpm": "rpm",
    "js.saving": "Saving …", "js.saved": "Saved.", "js.error_n": "Error {n}", "js.error_msg": "Error: {m}",
    "js.upload_progress": "{done} / {total} · new: {new} · known: {known} · errors: {err}",
    "js.upload_none": "No matching files found (expected: folder with ride_rec/, buried_rec/ or hms_rec/).",
    "js.reading_folder": "Reading folder …", "js.hide": "hide", "js.show": "show", "js.copied": "copied", "js.copy": "copy",
    "js.fw_uploading": "Uploading …", "js.fw_confirm_delete": "Delete this firmware version?",
}

WEEKDAYS = {
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}
MONTHS = {
    "de": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
}
MONTHS_SHORT = {
    "de": ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}

TEXTS: dict[str, dict[str, str]] = {"de": _DE, "en": _EN}


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT_LANG


def t(key: str, lang: str = DEFAULT_LANG, **kw) -> str:
    """Text zum Schlüssel; ``_default`` ersetzt den Schlüssel, wenn er fehlt."""
    default = kw.pop("_default", None)
    text = TEXTS[norm(lang)].get(key)
    if text is None:
        text = _DE.get(key)
    if text is None:
        return key if default is None else str(default)
    if kw:
        text = text.format(**kw)
    return Markup(text) if key.endswith("_html") else text


def js_texts(lang: str) -> dict[str, str]:
    """Texte für die Skripte (Präfix ``js.`` entfernt) plus die Sprache."""
    texts = {k[3:]: t(k, lang) for k in _DE if k.startswith("js.")}
    texts["lang"] = norm(lang)
    return texts


def pick_language(query: str | None, cookie: str | None, accept: str | None) -> str:
    """``?lang=`` vor Cookie vor ``Accept-Language`` vor Standard (de)."""
    for value in (query, cookie):
        if value in LANGS:
            return value
    ranked: list[tuple[float, int, str]] = []
    for i, part in enumerate((accept or "").split(",")):
        code, _, params = part.strip().partition(";")
        base = code.strip().lower().split("-")[0]
        q = 1.0
        if params.strip().startswith("q="):
            try:
                q = float(params.strip()[2:])
            except ValueError:
                q = 0.0
        if base in LANGS and q > 0:
            ranked.append((-q, i, base))
    return min(ranked)[2] if ranked else DEFAULT_LANG


# ----------------------------------------------------------------- Zahlen, Datum


def num(value: float | None, digits: int = 0, lang: str = DEFAULT_LANG) -> str:
    """Deutsch 1.234,5 – englisch 1,234.5."""
    if value is None:
        return "–"
    text = f"{value:,.{digits}f}"
    if norm(lang) == "de":
        text = text.replace(",", "X").replace(".", ",").replace("X", ".")
    return text


def date_text(d: date | datetime, lang: str = DEFAULT_LANG) -> str:
    return f"{d:%d.%m.%Y}" if norm(lang) == "de" else f"{d:%Y-%m-%d}"


def day_text(d: date, lang: str = DEFAULT_LANG) -> str:
    return f"{WEEKDAYS[norm(lang)][d.weekday()]} {date_text(d, lang)}"


def strftime(dt: datetime, fmt: str, lang: str = DEFAULT_LANG) -> str:
    """``strftime`` mit Wochentag und deutschen Formaten (``%d.%m.%Y``, `` Uhr``) je Sprache."""
    lang = norm(lang)
    if lang == "en":
        fmt = (fmt.replace("%d.%m.%Y", "%Y-%m-%d").replace("%d.%m.%y", "%y-%m-%d").replace("%d.%m.", "%m-%d")
               .replace(" Uhr", ""))
    return dt.strftime(fmt.replace("%a", WEEKDAYS[lang][dt.weekday()]))
