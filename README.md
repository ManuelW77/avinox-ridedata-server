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

## Server (Docker)

```sh
docker compose up -d --build
```

Danach läuft die Webseite unter `http://<host>:8000/`, die API-Doku unter `http://<host>:8000/api/docs`. Alle Daten (Rohdateien, Datenbank, Token) liegen im Volume unter `/data`.

**Upload-Token:** Ohne `AVINOX_UPLOAD_TOKEN` erzeugt der Server beim ersten Start ein zufälliges Token und legt es in `/data/upload_token` ab:

```sh
docker compose exec avinox cat /data/upload_token
```

Alternativ das Token per `AVINOX_UPLOAD_TOKEN` in `docker-compose.yml` fest setzen.

**Achtung:** Ohne `AVINOX_WEB_USER`/`AVINOX_WEB_PASSWORD` sind Webseite und Lese-API für jeden im LAN offen. Nicht ohne Passwort ins Internet stellen.

### Umgebungsvariablen

| Variable | Standard | Bedeutung |
|---|---|---|
| `AVINOX_DATA_DIR` | `./data` (im Container `/data`) | Rohdateien, Datenbank, Upload-Token |
| `AVINOX_UPLOAD_TOKEN` | zufällig, in `<data>/upload_token` | Bearer-Token für Uploads (ESP32) |
| `AVINOX_WEB_USER` / `AVINOX_WEB_PASSWORD` | leer | Optionaler Passwortschutz (Basic Auth) für Webseite und Lese-API |
| `AVINOX_TZ` | `Europe/Berlin` | Zeitzone für die Anzeige |
| `AVINOX_TILE_URL` | OpenStreetMap | Kachel-URL für die Karte |
| `AVINOX_TILE_ATTRIBUTION` | © OpenStreetMap | Quellenangabe der Karte |
| `AVINOX_MAX_UPLOAD_MB` | `64` | Größte erlaubte Datei |

### Upload-Protokoll (ESP32)

Alle Aufrufe mit `Authorization: Bearer <token>`. `path` ist der Pfad relativ zur Wurzel des Display-Laufwerks (z. B. `ride_rec/ride_rec_12.proto`).

1. `GET /api/v1/manifest` – bekannte Dateien (Pfad + Größe) und die angenommenen Pfadmuster.
2. `GET /api/v1/upload/status?path=…&size=…` – wie viele Bytes der Datei schon angekommen sind.
3. `PUT /api/v1/upload?path=…&size=…&offset=…` – rohe Bytes ab `offset` (stückweise möglich). Passt `offset` nicht zum Stand auf dem Server, kommt `409` mit dem tatsächlich empfangenen Stand; dort weitermachen.

```sh
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  --data-binary @ride_rec_12.proto \
  "http://<host>:8000/api/v1/upload?path=ride_rec/ride_rec_12.proto&size=$(stat -f%z ride_rec_12.proto)&offset=0"
```

(`stat -f%z` unter macOS, `stat -c%s` unter Linux.)

Ohne ESP32 lassen sich Dateien auch über die Webseite hochladen: **Einstellungen → Dateien hochladen**, dann den Ordner des Display-Laufwerks wählen. Verschlüsselte `ebike_log_*.dat` werden dabei ignoriert.

## Hinweise

- Das Dateiformat ist nicht offiziell dokumentiert. Die Feldzuordnung stammt aus Community-Reverse-Engineering, u. a. [lucad87/avinox-setup-app](https://github.com/lucad87/avinox-setup-app) (MIT), und eigenen Prüfungen. Werte vor dem Vertrauen gegen die Avinox-App prüfen.
- Fahrtdateien enthalten Seriennummer und GPS-Spur. Eigene Dateien nicht öffentlich teilen.

## Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE).
