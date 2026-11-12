# usb-probe – Machbarkeitstest ESP32-S3 als USB-Host

Prüft, ob ein ESP32-S3 das USB-Laufwerk des DJI-Avinox-Displays lesen kann. Das ist nur ein Test,
es gibt noch kein WLAN und keinen Upload. Auf das Laufwerk wird **nur lesend** zugegriffen.

Ablauf in einer Schleife:

1. Auf ein USB-Massenspeichergerät warten. Kommt nach 30 s keins, erscheint ein Hinweis auf VBUS und das Kabel.
2. VID/PID, Hersteller- und Produkt-String sowie Sektorgröße und -anzahl loggen. Die Seriennummer wird bewusst nicht geloggt.
3. Das Laufwerk als FAT unter `/usb` einhängen. Es wird nie formatiert und nie beschrieben.
4. Das Wurzelverzeichnis listen und `ride_rec/` rekursiv mit Dateigrößen listen.
5. Bei jeder `ride_rec_*.proto` prüfen, ob die ersten 4 Byte `A5 A5 A5 A5` sind (OK/FEHLT).
6. Die Größe von `buried_rec/buried_file_cur.json` loggen und die `ebike_log_*.dat` zählen. Gezählt werden nur die Namen, die Dateien werden nicht geöffnet.
7. Lesegeschwindigkeit messen: die größte `ride_rec_*.proto` in 4-KB-Blöcken komplett lesen und KB/s ausgeben.
8. Das Laufwerk aushängen, auf das Abziehen warten und von vorn beginnen.

## Bauen und Flashen

ESP-IDF v6.0.2 (EIM-Installation):

```sh
. ~/.espressif/tools/activate_idf_v6.0.2.sh   # oder: . ~/.espressif/v6.0.2/esp-idf/export.sh
cd firmware/usb-probe
idf.py set-target esp32s3
idf.py build
idf.py -p <port> flash monitor
```

`<port>` ist der **UART-Port** des Boards (beim DevKitC-1 die USB-Buchse mit der Aufschrift „UART“,
unter macOS etwa `/dev/cu.usbserial-*` oder `/dev/cu.SLAB_USBtoUART`). Der native USB-Port („USB“) wird
als Host gebraucht. Deshalb läuft die Konsole über UART0 (`CONFIG_ESP_CONSOLE_UART_DEFAULT=y`).

Die Komponenten `espressif/usb_host_msc` und `espressif/led_strip` lädt der Build aus der ESP Component Registry.
Die festgelegten Versionen stehen in `dependencies.lock`.

Die LED-GPIO lässt sich unter `idf.py menuconfig` → „USB-Probe“ → `PROBE_LED_GPIO` ändern (Standard 48, `-1` = aus).
Manche DevKitC-1-Revisionen haben die LED an GPIO 38.

## Verkabelung

Das Display arbeitet nur, wenn der Host **5 V auf VBUS** liefert und sich am USB-C-Anschluss
als Host zu erkennen gibt (CC-Pull-up). Empfohlene Kette:

```
nativer USB-Port des Boards → OTG-Adapter (USB-C-Stecker auf USB-A-Buchse)
→ Kabel USB-A-Stecker auf USB-C-Stecker → Display
```

Der Adapter bzw. das A-auf-C-Kabel stellt den CC-Pull-up bereit. Ein reines C-auf-C-Kabel direkt am Board reicht meist nicht.

**Vorher prüfen/messen:** Beim ESP32-S3-DevKitC-1 gibt der native USB-Port vermutlich nur dann 5 V
nach außen ab, wenn das Board über den 5V-Pin oder den UART-Port versorgt wird. Je nach Revision muss dafür
ggf. auch die USB-OTG-Lötbrücke (Jumper) geschlossen werden. Das ist nicht gesichert: Vor dem Anstecken des Displays
mit dem Multimeter am Ende des Kabels nachmessen, ob dort 5 V anliegen. Siehe auch Schaltplan und Doku
der eigenen Board-Revision.

## LED-Farben

| Farbe        | Bedeutung |
|--------------|-----------|
| blau blinkend | wartet auf Gerät |
| gelb          | Gerät erkannt, wird gelesen |
| grün          | Laufwerk vollständig gelesen, kann abgezogen werden |
| rot           | Fehler (Treiber, Enumeration, Mount oder Lesen) – siehe Log |

## Erwartete Logausgabe (gekürzt, Beispielwerte)

```
I (412) usb-probe: USB-Host und MSC-Treiber installiert
I (415) usb-probe: Warte auf USB-Massenspeicher ...
I (2310) usb-probe: MSC-Gerät angesteckt (usb_addr=1)
I (2400) usb-probe: VID=0x.... PID=0x.... Hersteller='...' Produkt='...'
I (2401) usb-probe: Sektorgröße=512 Sektoren=... (... MB)
I (2450) usb-probe: Laufwerk unter /usb eingehängt (nur lesend benutzt)
I (2460) usb-probe: --- Wurzelverzeichnis /usb ---
I (2470) usb-probe:   ride_rec/
I (2470) usb-probe:   buried_rec/
I (2480) usb-probe: --- ride_rec/ rekursiv ---
I (2490) usb-probe:       123456  /usb/ride_rec/ride_rec_1008.proto
I (2495) usb-probe:   Magic /usb/ride_rec/ride_rec_1008.proto: A5 A5 A5 A5 -> OK
I (2600) usb-probe: ride_rec_*.proto: 12 Dateien, 12 mit Magic A5A5A5A5
I (2601) usb-probe: buried_rec/buried_file_cur.json: 2048 Byte
I (2602) usb-probe: ebike_log_*.dat im Wurzelordner: 30
I (2603) usb-probe: Speedtest mit größter Datei (... Byte)
I (4800) usb-probe: Speedtest: ... Byte in 2.19 s = ... KB/s (...)
I (4810) usb-probe: Laufwerk ausgehängt
I (4811) usb-probe: Ergebnis: LAUFWERK GELESEN. Bitte Display abziehen.
```

Kommt kein Gerät:

```
W (30420) usb-probe: Seit 30 s kein Gerät. VBUS (5 V am Display?), OTG-Adapter und Kabel prüfen.
```
