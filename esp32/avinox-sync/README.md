# avinox-sync – ESP32-S3 lädt Fahrten vom Display auf den Server

Firmware für einen ESP32-S3, der am USB-Anschluss des Avinox-Displays hängt. Sie liest neue Dateien vom
USB-Laufwerk des Displays (**nur lesend**) und lädt sie per WLAN auf den eigenen Server (`server/`) hoch.
Das Display versorgt den ESP32 mit Strom, er läuft also, solange das Bike an ist.

Basis ist der Machbarkeitstest [`../usb-probe/`](../usb-probe/): gleiche gepatchte USB-Komponente, gleiche
FIFO-Aufteilung, gleiche MSC-Einbindung. Warum das nötig ist (Display meldet bei Full Speed Bulk-MPS 512),
steht in `ERKENNTNISSE.md` Abschnitt 3b und in `components/usb/PATCH.md`.

## Bauen und Flashen

ESP-IDF v6.0.2:

```sh
. ~/.espressif/tools/activate_idf_v6.0.2.sh
cd esp32/avinox-sync
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/cu.usbmodem* flash monitor
```

Geflasht und geloggt wird über die **UART-Buchse** (CH343, am Mac `/dev/cu.usbmodem…`). Die native Buchse
gehört dem Display. Die Komponenten `espressif/usb_host_msc` (1.3.0), `espressif/led_strip` und `espressif/cjson` lädt
der Build aus der ESP Component Registry, `espressif/usb` 1.6.0 kommt gepatcht aus `components/usb/`
(per `override_path`, siehe `main/idf_component.yml`).

Einstellungen unter `idf.py menuconfig` → „Avinox-Sync“:

| Option | Standard | Bedeutung |
|---|---|---|
| `AVX_LED_GPIO` | 48 | WS2812-Status-LED, `-1` = aus (manche Boards: 38) |
| `AVX_BOOT_GPIO` | 0 | BOOT-Taste (low-aktiv) |
| `AVX_SYNC_INTERVAL_MIN` | 10 | Abstand zwischen zwei Syncs |
| `AVX_NO_DRIVE_HINT_S` | 30 | Sekunden ohne Laufwerk bis die LED orange blinkt |
| `AVX_CHUNK_SIZE` | 32768 | Größe eines Upload-Stücks in Byte |
| `AVX_OTA_CHECK_S` | 21600 | Abstand der Firmware-Update-Prüfung (zusätzlich nach jedem Sync) |
| `AVX_OTA_VALIDATE_S` | 300 | Frist, in der eine neue Firmware den Server erreichen muss, sonst Rückkehr zur alten |

Flash-Aufteilung (`partitions.csv`, eingestellt sind 4 MB, das Board hat 16 MB): NVS 64 KB, phy_init, App 3 MB.

## Verkabelung

```
native USB-C-Buchse des ESP32-S3  ── USB-C-Kabel ──  Display
UART-Buchse (CH343)               ── nur zum Flashen/Loggen am Mac
```

Direkt per USB-C, das Display liefert den Strom. Für das Log im Betrieb kann der Mac gleichzeitig an der UART-Buchse
hängen.

## Ersteinrichtung

1. Upload-Token vom Server holen:
   `docker compose exec avinox cat /data/upload_token`
   (oder auf der Seite „Einstellungen“ des Servers, dort nur sichtbar, wenn ein Web-Passwort gesetzt ist).
2. ESP32 starten (am Display oder am Mac). Ohne vollständige Konfiguration geht er in den **Einrichtungsmodus**,
   die LED blinkt violett.
3. Mit dem Handy/Laptop ins WLAN `Avinox-Sync-XXXX` (XXXX = letzte 4 Hex-Stellen der MAC). Beim ersten Mal ist es
   **offen** (ohne Passwort). Die Einrichtungsseite öffnet sich meist von selbst (Captive Portal), sonst
   http://192.168.4.1 aufrufen.
4. WLAN aus der Liste wählen oder den Namen eintragen, WLAN-Passwort, Server-URL (z. B. `http://192.168.1.10:8000`,
   http oder https) und Token eintragen. Außerdem im Abschnitt „Passwort für dieses Einrichtungs-WLAN“ ein eigenes Passwort
   (mindestens 8 Zeichen, zweimal) vergeben, das ist beim ersten Mal Pflicht. „Speichern und neu starten“.
   Ab dem nächsten Einrichtungsmodus ist das WLAN mit diesem Passwort (WPA2) geschützt; leer lassen = unverändert.

Passwort und Token werden nie zurück ins Formular geschrieben, die Seite zeigt nur „gesetzt“. Leer lassen heißt
unverändert. Für ein offenes WLAN das Häkchen „Offenes WLAN“ setzen. Die Seite zeigt oben auch den Status mit dem
letzten Sync-Ergebnis (zusätzlich unter `/status`).

**Neu einrichten:** Im Betrieb (nach dem Einschalten) die BOOT-Taste 3 s halten (LED wird violett) und loslassen →
Neustart in den Einrichtungsmodus.

**Werkseinstellung:** BOOT-Taste 10 s halten (LED blinkt schnell rot) → gesamte Konfiguration inkl. Passwort des
Einrichtungs-WLANs wird gelöscht, Neustart, offenes Einrichtungs-WLAN. Zum Neustart die Taste loslassen.
Nicht schon beim Einschalten drücken, sonst startet der ESP32 im Download-Modus (BOOT = GPIO 0 ist ein Strapping-Pin).
„Ohne Änderung neu starten“ verlässt den Einrichtungsmodus wieder.

Gespeichert wird im NVS, Namespace `cfg`: `wifi_ssid`, `wifi_pass`, `server_url`, `token`, `ap_pass`.

## LED

| Farbe | Bedeutung |
|---|---|
| weiß, kurz | Start |
| violett blinkend | Einrichtungsmodus |
| violett dauerhaft | BOOT ≥ 3 s gedrückt (Loslassen = Einrichtungsmodus) |
| rot schnell blinkend | BOOT ≥ 10 s: Werkseinstellung |
| blau blinkend | wartet auf Laufwerk und/oder WLAN |
| orange blinkend | seit 30 s (`AVX_NO_DRIVE_HINT_S`) kein Laufwerk gefunden: Display eingeschaltet? An der nativen USB-Buchse? |
| cyan | Sync läuft |
| weiß langsam blinkend | Firmware-Update wird geladen |
| grün | Sync ok, alles aktuell |
| gelb | Sync mit Fehlern (Server nicht erreichbar, Token falsch, Datei nicht hochgeladen …) – siehe Log |
| rot | schwerer Fehler: USB-Host startet nicht, Laufwerk lässt sich nicht anmelden (bis zum Abziehen), NVS kaputt, Einrichtungs-WLAN startet nicht |

**Hinweis:** Das Display muss an der **nativen USB-Buchse** des Boards hängen. Die UART-Buchse (CH343) ist selbst ein
USB-Gerät und kann kein Laufwerk lesen.

## Ablauf

1. Start: NVS laden. Fehlt SSID, Server-URL oder Token (oder ist der Merker von der BOOT-Taste gesetzt) →
   Einrichtungsmodus.
2. Normalbetrieb: WLAN (Station) und USB-Host starten parallel. Kein WLAN nach 30 s → alle 60 s neuer Versuch
   (unterwegs gibt es kein WLAN, kein Deep Sleep).
3. Sobald Laufwerk angemeldet **und** WLAN verbunden:
   - `GET {server}/api/v1/manifest` mit `Authorization: Bearer <token>` → bekannte Dateien (Pfad + Größe).
   - Laufwerk einhängen (FAT, nie formatieren, nur lesen) und gezielt durchsuchen: nur `ride_rec/` (eine Ebene
     Unterordner) sowie `buried_rec/buried_file_cur.json` und `hms_rec/hms_current_storage.proto`. Angenommen werden
     dieselben Pfade wie auf dem Server (`store.py`, `PATTERNS`):
     `ride_rec/[<ordner>/]ride_rec_<zahl>.proto`, `ride_rec/[<ordner>/]ride_rec_index.dat`,
     `buried_rec/buried_file_cur.json`, `hms_rec/hms_current_storage.proto`.
     Die ~2500 `ebike_log_*.dat` im Wurzelordner werden weder gelistet noch geöffnet.
   - Neu = (Pfad, Größe) nicht im Manifest. Dateizeiten werden ignoriert (Display liefert Jahr 2098).
   - Je neue Datei: `GET /api/v1/upload/status?path=…&size=…` → `received`; ab dort Stücke zu 32 KB per
     `PUT /api/v1/upload?path=…&size=…&offset=…` (rohe Bytes, `Content-Type: application/octet-stream`).
     409 → beim gemeldeten `received` weitermachen. Netzfehler/5xx → bis zu 3 Wiederholungen (2/5/10 s Pause),
     danach Datei überspringen, der nächste Sync setzt beim Server-Stand fort. 401/403 → Sync abbrechen.
   - Laufwerk aushängen, Zusammenfassung loggen und im NVS speichern (Namespace `sync`, Blob `last`: Start-Nr.,
     Laufzeit, Dateien geprüft/neu/übersprungen/Fehler, Bytes, Dauer, Meldung).
4. Erneuter Sync alle 10 min, solange Laufwerk und WLAN da sind (Fahrten wachsen während der Fahrt; bekannte Dateien
   kosten nur das Manifest), und sofort nach erneutem Anstecken oder neuer WLAN-Verbindung.

## Rückmeldung an den Server

Jede Anfrage trägt den Header `X-Avinox-Device: avinox-sync-XXXX` (XXXX = wie im Namen des Einrichtungs-WLANs). Zusätzlich meldet
das Gerät per `POST /api/v1/device/report` (Bearer-Token, JSON; Firmware, Laufzeit, RSSI, IP, freier Heap, Laufwerk):

- **Heartbeat** sofort nach jeder WLAN-Verbindung und dann alle 5 min (Kconfig `AVX_HEARTBEAT_S`), auch ohne Laufwerk.
- **Sync-Bericht** nach jedem Sync-Lauf (auch bei Fehlern/Abbruch) mit Zählern, Bytes, Dauer, Ergebnis `ok`/`warn`/`error`
  und dem Fehlertext.

Sendefehler werden nur geloggt (Timeout 10 s, keine Wiederholung). Der Server zeigt das auf der Seite `/sync`.

Der Token wird nie geloggt, das WLAN-Passwort auch nicht. Die Seriennummer des Displays wird nicht geloggt.

## Updates per WLAN (OTA)

**Einmalig per Kabel flashen** (`idf.py -p PORT flash`): Ab 0.3.0 gilt eine neue Partitionstabelle
(`nvs` 0x9000, `otadata`, `phy_init`, `ota_0`/`ota_1` je 0x1F0000, kein `factory`). `nvs` liegt unverändert, die
Konfiguration bleibt erhalten.

Danach:

1. `PROJECT_VER` in `CMakeLists.txt` erhöhen (vor **jedem** Update – gleiche Version wird nicht installiert).
2. `idf.py build` → `esp32/avinox-sync/build/avinox-sync.bin` im Server unter *Einstellungen → Firmware* hochladen
   (oder `avinox firmware add`).
3. Der ESP fragt nach jedem abgeschlossenen Sync (nie während eines Uploads) und spätestens alle 6 h
   (`AVX_OTA_CHECK_S`; erstmals 30 s nach der ersten WLAN-Verbindung)
   `GET /api/v1/firmware/latest?project=avinox-sync` ab. Andere Version → Bericht `ota`/`started`, Download in die
   freie OTA-Partition, Prüfung von Größe, SHA-256 und Projektname, Umschalten, Bericht `ok`, Neustart. Fehler →
   Bericht `failed` mit Text, die laufende Firmware bleibt; dieselbe Version wird erst beim nächsten periodischen
   Prüfzeitpunkt erneut versucht. Die Download-Adresse muss ein relativer Pfad unter `/api/v1/firmware/` sein
   (wird an die Server-URL gehängt, Weiterleitungen werden nicht verfolgt), sonst Abbruch mit `failed` – so gelangt das
   Token nie zu einem fremden Host.
4. **Automatische Rückkehr:** Die neue Firmware gilt erst als gültig, wenn WLAN verbunden ist und der Server einmal
   erfolgreich geantwortet hat (Heartbeat/Bericht 2xx oder Manifest 200). Klappt das nicht binnen 5 min
   (`AVX_OTA_VALIDATE_S`), startet der ESP neu und der Bootloader kehrt zur vorherigen Version zurück; die meldet dann
   einmal `ota`/`rolled_back`. Ohne Konfiguration (Einrichtungsmodus) wird die neue Firmware sofort bestätigt.

## Code

| Datei | Inhalt |
|---|---|
| `main/main.c` | Ablauf, BOOT-Taste, Sync-Schleife, LED-Zustand |
| `main/config.c/.h` | Konfiguration im NVS |
| `main/portal.c/.h` | SoftAP, HTTP-Server mit Einrichtungs- und Statusseite, Captive-Portal-DNS |
| `main/wifi_sta.c/.h` | WLAN-Station mit 30-s-Fenster / 60-s-Pause |
| `main/usb_drive.c/.h` | USB-Host + MSC (aus usb-probe), Einhängen unter `/usb` |
| `main/sync.c/.h` | Manifest, Upload-Status, Upload in Stücken, Ergebnis im NVS |
| `main/sync_paths.c/.h` | Pfadfilter, Manifest-Menge, URL-Kodierung (ohne IDF, Host-Test) |
| `main/led.c/.h` | Status-LED |
| `main/ota.c/.h` | Firmware-Update per WLAN, Bestätigung/Rollback |
| `test/host/test_paths.c` | Host-Test für `sync_paths.c` |

Host-Test (ohne ESP-IDF):

```sh
cd esp32/avinox-sync/test/host
cc -Wall -Wextra -I../../main ../../main/sync_paths.c test_paths.c -o test_paths && ./test_paths
```

## Bekannte Grenzen

- **Am Gerät noch ungetestet.** USB-Teil ist aus usb-probe übernommen (dort am Display getestet), WLAN, Portal und
  Upload nur gebaut.
- Das Display schreibt während der Fahrt selbst auf das Laufwerk. FAT ist nicht für zwei Schreiber/Leser gleichzeitig
  gedacht: eine Datei kann beim Lesen gerade wachsen. Deshalb wird pro Sync neu eingehängt und die beim Durchsuchen
  ermittelte Größe hochgeladen; die gewachsene Fassung kommt beim nächsten Sync (anderer Pfad+Größe). Liest der ESP32
  einen inkonsistenten Stand, landet eine fehlerhafte Fassung auf dem Server – der Server ersetzt eine Fahrt nicht
  durch eine kleinere Fassung.
- Nur eine Ordnerebene unter `ride_rec/`, höchstens 512 passende Dateien pro Sync, Manifest-Antwort bis 256 KB.
- https nutzt das eingebaute CA-Bundle von ESP-IDF; selbstsignierte Zertifikate werden nicht akzeptiert.
- Die WLAN-Suche im Einrichtungsmodus läuft beim Start und über „Netze neu suchen“; dabei kann die Verbindung zum
  Handy kurz abreißen.
- Leere Dateien (0 Byte) werden übersprungen.
- Der Einrichtungsmodus bleibt aktiv, bis gespeichert oder neu gestartet wird (kein Timeout).
