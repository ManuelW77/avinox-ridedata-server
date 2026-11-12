<p align="center"><img src="assets/icon/icon-rounded-512.png" width="128" alt="Icon"></p>

# avinox-ridedata-server

Fahrdaten vom DJI-Avinox-E-Bike-Antrieb direkt vom USB-Laufwerk des Displays auslesen und auswerten – ohne Avinox-Cloud und ohne Zugangsdaten.

Geplant: Ein ESP32-S3 holt neue Fahrten per USB vom Display und lädt sie per WLAN auf einen Server (Docker), der sie auswertet und als Webseite mit Statistiken bereitstellt.

## Stand

- Parser für die Fahrtaufzeichnungen `ride_rec/**/ride_rec_<id>.proto` (Header, Frames, Samples mit ca. 1 Hz)
- Kennzahlen je Fahrt wie in der Avinox-App (vom Display berechnet und im Header gespeichert)
- Auswertung: Zeit je Unterstützungsmodus und Gang, Verteilungen (Leistung, Trittfrequenz, Tempo, Steigung, Puls), Bestleistungen 5 s – 60 min, Energie und Motoranteil, Akkuverbrauch, Stopps, Zeitreihe für Diagramme
- GPS-Strecke als GeoJSON (vereinfacht, mit Messwerten je Punkt) und GPX (mit Leistung, Trittfrequenz, Puls)
- Nutzungsstatistik `buried_rec/buried_file_cur.json`: Firmware, Akku (Zellspannungen, Strom, Temperaturen), Sekunden je Gang/Modus, Neigung, Fernbedienung – automatisch der Fahrt zugeordnet
- Meldungen `hms_rec/hms_current_storage.proto`

## Benutzung

```sh
uv sync
uv run avinox summary /Volumes/<display>/ride_rec                 # Tabelle aller Fahrten
uv run avinox analyze /Volumes/<display>/ride_rec/<…>.proto       # vollständige Auswertung (JSON)
uv run avinox gpx     /Volumes/<display>/ride_rec/<…>.proto -o fahrt.gpx
uv run avinox geojson /Volumes/<display>/ride_rec/<…>.proto -o fahrt.geojson
uv run avinox device  /Volumes/<display>/buried_rec/buried_file_cur.json
uv run pytest
```

## Hinweise

- Das Dateiformat ist nicht offiziell dokumentiert. Die Feldzuordnung stammt aus Community-Reverse-Engineering, u. a. [lucad87/avinox-setup-app](https://github.com/lucad87/avinox-setup-app) (MIT), und eigenen Prüfungen. Werte vor dem Vertrauen gegen die Avinox-App prüfen.
- Fahrtdateien enthalten Seriennummer und GPS-Spur. Eigene Dateien nicht öffentlich teilen.

## Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE).
