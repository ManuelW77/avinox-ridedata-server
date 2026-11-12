# Erkenntnisse: Avinox-Fahrdaten

Stand: 12.11.2026. Bike: Amflow PR mit DJI Avinox, App auf dem iPhone.

## 1. Wege zu den Daten

| Weg | Ergebnis |
|---|---|
| Display per USB-C ans iPhone/Rechner | **Funktioniert.** Display meldet sich als USB-Laufwerk. So liegen die Daten in `_demo-data/`. |
| Strava-Auto-Sync | Offiziell, aber dünn: FIT mit Fahrerleistung, Motorleistung nicht belegt. Auflösung laut Forum 10 s, nach Update evtl. 1 s. |
| ANT+ / BLE an Garmin | Geht nicht. Display empfängt nur (Pulsgurt), sendet keine E-Bike-Daten (kein ANT+ LEV). |
| Live-BLE mitlesen | Kein öffentlicher Decoder. BikeBridge (codeberg.org/bg443/BikeBridge) forscht daran. |
| Avinox-Cloud direkt | **Bewusst nicht verfolgt.** Die API (`api.avinox-ebike.com`) verlangt signierte Anfragen; die Signierung steckt in der nativen DJI-Bibliothek der App. Der Cloud-Import von avinox-beta.avinoxride.workers.dev läuft über den Server des Betreibers, der dabei die Avinox-Zugangsdaten durchreicht. Kein öffentlicher Client vorhanden, wäre instabil und ein Zugangsdaten-Risiko. |

## 2. Inhalt des Display-Laufwerks (`_demo-data/`, ca. 3 GB)

| Pfad | Inhalt |
|---|---|
| `ride_rec/ride_rec_<id>.proto` | **Fahrtaufzeichnungen.** Das ist die Hauptquelle. |
| `ride_rec/<konto-id>/ride_rec_<id>.proto` | Fahrten nach der Kopplung mit der App, Unterordner = ID im Header (Offset 128, vermutlich Konto). Zeiten als Unix-Zeit. |
| `ride_rec/ride_rec_<id>.proto` (Hauptordner) | Erste Fahrten vor der Kopplung. Zeiten = Sekunden seit Geräte-Start, **keine Uhrzeit**. |
| `ride_rec/**/ride_rec_index.dat` | 24 Byte: uint32 erste ID, letzte ID, letzte ID, ein weiterer Wert; Rest 0. |
| `ebike_log_<n>_<datum>_<zeit>.dat` | 2482 Systemlogs, zusammen ca. 3 GB, einzeln bis 268 MB. Kennung `DGS_EB100` / `DJI_LOG_V3`. Inhalt **verschlüsselt** (Entropie 7,5–7,8 Bit/Byte, sich wiederholende 8-Byte-Blöcke). Ohne DJI-Schlüssel nicht lesbar, wird nicht verfolgt. |
| `buried_rec/buried_file_cur.json` | **Nutzungsstatistik** („buried point“ = Analytics), JSON-Zeilen mit `event_id`, ca. 1000 Zeilen, siehe unten. Lesbar. |
| `hms_rec/hms_current_storage.proto` | 48 Byte, Magic `0xA5A5A5A5`, Seriennummer, ein Frame Typ `0x01EB` mit Protobuf. Vermutlich Fehler-/Gesundheitsmeldungen (DJI „HMS“). Noch nicht dekodiert. |
| `System/ebike_log_index.dat` | 12 Byte, uint32 = Anzahl der `ebike_log`-Dateien (2482). |
| `System/map/tracks/<hash>/` | Nur eine leere `thumbnail.jpg.tmp`. |

Hinweis: Die Dateizeiten auf dem Laufwerk sind unbrauchbar (Jahr 2098). Neue Dateien also über Name + Größe erkennen, nicht über Änderungszeit.

## 3. Format der `ride_rec_*.proto` (geprüft an `ride_rec_1002.proto`)

Quelle der Feldzuordnung: Community-Reverse-Engineering, v. a. `lucad87/avinox-setup-app` (MIT), Datei `public/avinox-proto-parser.js`. Nicht offiziell, nicht über alle Firmwarestände verifiziert.

**Header (251 Byte, little-endian)**
- Offset 0: Magic `0xA5A5A5A5` · Offset 4: uint16 = 8 (vermutlich Formatversion)
- Offset 6: rideId (uint32). **IDs wiederholen sich zwischen Hauptordner und Konto-Ordner**, eindeutig ist nur der Pfad.
- Offset 10 / 14: Start / Ende (uint32), Unix-Zeit oder Sekunden seit Geräte-Start (siehe oben)
- Offset 18: Distanz in m (uint32), = letzter Wert von Feld 23
- Offset 22 / 26: Höhengewinn / Höhenabnahme (float32), **= App-Werte** (geprüft). Deutlich kleiner als aus den Höhen-Samples (dort ca. 165 / 161 statt 73 / 13).
- Offset 30–109: 10 Vorschaupunkte (int32 Breite, int32 Länge, je × 1e7), liegen exakt auf der GPS-Spur; 0 ohne GPS
- Offset 110–127: Seriennummer (ASCII, nullterminiert) – privat
- Offset 128: Konto-ID mit Längenbyte (ASCII), leer im Hauptordner – privat
- Offset 161–236: **Kennzahlen, die die App anzeigt** (19 × float32, geprüft an Fahrt vom 05.11.2026 gegen App; Gesamt = Fahrer + Motor geht in allen 12 Fahrten exakt auf):
  Ø / max Steigung % · Ø / max Tempo km/h · Gesamt Ø / max Leistung W · Gesamt Ø / max Drehmoment Nm · Motor Ø / max Leistung · Motor Ø / max Drehmoment · Fahrer Ø / max Leistung · Fahrer Ø / max Drehmoment · Ø / max Trittfrequenz · Kalorien × 1000
  Die Durchschnitte rechnet das Display selbst; sie lassen sich aus den Samples nicht nachbilden (Ø Tempo 6,3 km/h, Distanz/Dauer ergäbe 8,6). Die App schneidet Nachkommastellen ab (13,77 → 13 U/min), Steigung zeigt sie in Grad.
- Offset 237–244: 0
- Offset 245: Anzahl Samples (uint32) = **Dauer in der App** (Sekunden). Manchmal ein Frame mehr in der Datei.
- Offset 249–250: vermutlich Prüfsumme

**Frames ab Offset 251**
- `uint16 typ (0x02EB) | uint16 länge | Protobuf (länge Byte) | 2 Byte Prüfsumme`
- Prüfsumme: Verfahren offen (CRC-16/0x1021-Varianten passen nur auf einzelne Frames). Zum Lesen nicht nötig.
- Geprüft an 12 Fahrten / 26 599 Frames: alle Typ `0x02EB`, keine Restbytes, ca. 1 Sample pro Sekunde (Zeitsprünge 0–2 s, Pausen länger)

**Sample-Felder (Protobuf-Feldnummer → Kanal, Skalierung)**

Fehlende Felder: Protobuf lässt Nullwerte weg. Leistung, Geschwindigkeit, Stufe usw. sind dann 0; Höhe, GPS, Temperatur, Druck, Puls dagegen unbekannt.

| Feld | Kanal | Skalierung |
|---|---|---|
| 6 | laufender Zähler | roh |
| 7 | Geschwindigkeit km/h | ÷100 |
| 8 | Unterstützungsstufe | roh (Werte 0–5, 20, 21) |
| 9 | Trittfrequenz U/min | ÷100 |
| 10 / 11 / 12 | Drehmoment Motor / Fahrer / gesamt Nm | ÷100 |
| 13 / 14 / 15 | Leistung Fahrer / Motor / gesamt W | ÷100 |
| 22 | Gang | roh (1–12) |
| 23 | Distanz der Fahrt in m | roh (geprüft gegen Kilometerstand, GPS und Geschwindigkeit) |
| 31 / 32 | Breite / Länge | float64 |
| 36–38 | IMU x/y/z | float32 |
| 39 / 40 | Höhe m / Steigung % | signed ÷100 |
| 41 | Temperatur °C | signed ÷100, -99900 → unbekannt |
| 42 | Luftdruck **kPa** | ÷100 (z. B. 101,49) |
| 51 | Puls | roh |
| 52 | Kalorien kcal bisher | ÷1000; letzter Wert = Header-Kalorien. Läuft bei kurz aufeinanderfolgenden Fahrten teils weiter |
| 71 | Akku % | roh |
| 72 | Zeitstempel | wie Header-Start |
| 73 | Kilometerstand in m | roh |
| 75 | Ereignis | roh (bisher 0 und 2) |

Parser: `src/avinox/ride_rec.py`, Kennzahlen: `src/avinox/summary.py`.

## 3a. Nutzungsstatistik `buried_rec/buried_file_cur.json`

JSON pro Zeile, Zeitstempel `time_now` als `J-M-T-h:m:s` (vor Uhrzeit-Sync 1970). Werte als Strings. Mehrere Blöcke pro Fahrt bzw. Einschaltvorgang (`boot_times`). Gefundene `event_id` (Auswahl):

| event_id | Inhalt |
|---|---|
| `Firmware_version` | Versionen Paket, Display, Motor, BLE, Akku |
| `single_ride` / `single_power` | Distanz, Akku, Starthöhe / Distanz, Dauer |
| `rider_cadence`, `rider_torque`, `rider_heart_rate` | Ø / max (= Header-Werte der Fahrt) |
| `ride_power`, `motor_torque` | Histogramme: Sekunden je Leistungs- bzw. Drehmomentstufe (`power_700`, `torque_110` …) |
| `gear_shift` | Schaltvorgänge je Gang |
| `assist_mode_usage`, `assist_mode_order`, `boost_mode`, `ass_mode_para` | Dauer je Unterstützungsmodus, Wechsel, Boost, Modus-Parameter |
| `battery_usage`, `battery_temperture`, `battery_circle` | max. Zellspannungsdifferenz, min. Gesamtspannung, Temperaturgrenzen, Ladezyklen |
| `mos_ntc_temperture`, `bike_pitch_attitude`, `bike_horizontal_attitude` | Temperaturbegrenzung Motor-Elektronik, Zeit mit > 40° Neigung / Schräglage |
| `wireless_controller_usage` / `_customize` | Tastendrücke an der Fernbedienung, Belegung |

Zuordnung zu einzelnen Fahrten: über Zeitstempel bzw. Distanz (`single_ride.distance` = Fahrtdistanz).

## 4. Bestehende Community-Projekte

- `lucad87/avinox-setup-app` (MIT): Parser + Setup-Rechner, gehostet avinox-calculator.lucad.cloud
- Avinox Ride Explorer (avinox.avinoxride.workers.dev): Anzeige, Export FIT/TCX/GPX, lokal im Browser
- `AndrewAtkinson/avinox`, `sgmess/avinox`: Dashboards
- `Pr0zak/myvitals`: holt fertig ausgewertete Fahrten per JSON von einem eigenen Avinox-Server (Felder u. a. `avg_rider_power_w`, `avg_motor_power_w`, `avg_cadence`, `hr_samples`) – nützliche Vorlage für die eigene Server-API
- `chaffed/avinox-ride-privacy-review`: Analyse der App (Hosts, Funktionen)
