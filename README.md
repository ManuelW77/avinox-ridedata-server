<p align="center"><img src="assets/icon/icon-rounded-512.png" width="128" alt="Icon"></p>

# avinox-ridedata-server

Fahrdaten vom DJI-Avinox-E-Bike-Antrieb direkt vom USB-Laufwerk des Displays auslesen und auswerten – ohne Avinox-Cloud und ohne Zugangsdaten.

Aufbau: ESP32-S3 (holt neue Fahrten per USB vom Display) → WLAN → Server (Docker, wertet aus) → Webseite mit Statistiken.

## Ordner

| Ordner / Datei | Inhalt |
|---|---|
| [`server/`](server/README.md) | Parser, Auswertung, Server, Docker |
| [`esp32/`](esp32/usb-probe/README.md) | Firmware; derzeit `usb-probe` = Machbarkeitstest |
| [`ERKENNTNISSE.md`](ERKENNTNISSE.md) | Dateiformat, Datenwege, Feldzuordnung |
| `assets/` | Icon |

Details: [server/README.md](server/README.md), [esp32/usb-probe/README.md](esp32/usb-probe/README.md).

## Hinweise

- Das Dateiformat ist nicht offiziell dokumentiert. Die Feldzuordnung stammt aus Community-Reverse-Engineering, u. a. [lucad87/avinox-setup-app](https://github.com/lucad87/avinox-setup-app) (MIT), und eigenen Prüfungen. Werte vor dem Vertrauen gegen die Avinox-App prüfen.
- Fahrtdateien enthalten Seriennummer und GPS-Spur. Eigene Dateien nicht öffentlich teilen.

## Lizenz

GPL-3.0-or-later, siehe [LICENSE](LICENSE).
