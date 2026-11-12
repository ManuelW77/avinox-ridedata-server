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
| `ride_rec/<nummer>/ride_rec_<id>.proto` | Weitere Fahrten in Unterordner (hier `<konto-id>/`, 9 Fahrten). Header-Startzeit dort als Unix-Zeit. |
| `ride_rec/ride_rec_index.dat` | 24-Byte-Index (erste/letzte ID u. a.), noch nicht dekodiert. |
| `ebike_log_<n>_<datum>_<zeit>.dat` | 2482 Systemlogs, zusammen ca. 3 GB, einzeln bis 268 MB. Anderes Format (kein `0xA5A5A5A5`), Inhalt unbekannt. Für die Auswertung vorerst nicht nötig. |
| `hms_rec/`, `buried_rec/buried_file_cur.json`, `System/` | Wartungs-/Diagnosedaten, Index, Kartenvorschau. Noch nicht untersucht. |

Hinweis: Die Dateizeiten auf dem Laufwerk sind unbrauchbar (Jahr 2098). Neue Dateien also über Name + Größe erkennen, nicht über Änderungszeit.

## 3. Format der `ride_rec_*.proto` (geprüft an `ride_rec_1002.proto`)

Quelle der Feldzuordnung: Community-Reverse-Engineering, v. a. `lucad87/avinox-setup-app` (MIT), Datei `public/avinox-proto-parser.js`. Nicht offiziell, nicht über alle Firmwarestände verifiziert.

**Header (251 Byte, little-endian)**
- Offset 0: Magic `0xA5A5A5A5` (bestätigt)
- Offset 6: rideId (uint32), z. B. 1002
- Offset 10 / 14: Start / Ende (uint32). Im Hauptordner offenbar Sekunden seit Geräte-Start, im Unterordner Unix-Zeit. Echte Zeit pro Sample steht in Feld 72.
- Offset 22 / 26: Anstieg / Abstieg (float32)
- Offset 110–127: Seriennummer (ASCII, nullterminiert)

**Frames ab Offset 251**
- Byte 0–1: Typ `0x02EB`, Byte 2–3: Länge (uint16), Byte 4–5: unbekannt, danach Protobuf-Nachricht = 1 Sample
- Geprüft: 1186 Frames bei 1186 s Fahrzeit → **ca. 1 Sample pro Sekunde**, Datei endet exakt auf Frame-Grenze

**Sample-Felder (Protobuf-Feldnummer → Kanal, Skalierung)**

| Feld | Kanal | Skalierung |
|---|---|---|
| 7 | Geschwindigkeit | ÷100 |
| 8 | Unterstützungsstufe | roh |
| 9 | Trittfrequenz | ÷100 |
| 10 / 11 / 12 | Drehmoment Motor / Fahrer / gesamt | ÷100 |
| 13 / 14 / 15 | Leistung Fahrer / Motor / gesamt | ÷100 |
| 22 | Gang | roh |
| 23 | Distanz km | ÷1000 |
| 31 / 32 | Breite / Länge | float64 aus Bitmuster |
| 36–38 | IMU x/y/z | float32 |
| 39 / 40 | Höhe / Steigung | signed ÷100, fehlt → null |
| 41 | Temperatur | signed ÷100, -99900 → null |
| 42 | Luftdruck | ÷100, 0 → null |
| 51 | Puls | roh |
| 52 | Fahrerenergie kJ | ÷1000 |
| 71 | Akku % | roh |
| 72 | Zeitstempel | Unix-Sekunden |
| 73 | Gesamt-km | ÷1000 |
| 75 | Ereignis | roh |

## 4. Bestehende Community-Projekte

- `lucad87/avinox-setup-app` (MIT): Parser + Setup-Rechner, gehostet avinox-calculator.lucad.cloud
- Avinox Ride Explorer (avinox.avinoxride.workers.dev): Anzeige, Export FIT/TCX/GPX, lokal im Browser
- `AndrewAtkinson/avinox`, `sgmess/avinox`: Dashboards
- `Pr0zak/myvitals`: holt fertig ausgewertete Fahrten per JSON von einem eigenen Avinox-Server (Felder u. a. `avg_rider_power_w`, `avg_motor_power_w`, `avg_cadence`, `hr_samples`) – nützliche Vorlage für die eigene Server-API
- `chaffed/avinox-ride-privacy-review`: Analyse der App (Hosts, Funktionen)
