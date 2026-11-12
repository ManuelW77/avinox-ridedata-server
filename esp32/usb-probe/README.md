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
cd esp32/usb-probe
idf.py set-target esp32s3
idf.py build
idf.py -p <port> flash monitor
```

`<port>` ist der **native USB-Port** (USB-Serial-JTAG), am Mac `/dev/cu.usbmodem*`. Erscheint er nicht,
das Board im Download-Modus starten: BOOT halten, RESET drücken, BOOT loslassen.
Die Konsole läuft über USB-Serial-JTAG (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`). Sobald der USB-Host
installiert ist, schaltet der PHY auf OTG, und die Konsole ist weg. Das ist gewollt.

## Ablauf mit nur einem Datenport

Das Board hat nur einen datenfähigen Port, und der muss beim Test am Display hängen. Deshalb schreibt die
Firmware ihren Log als Bericht mit und speichert ihn nach jedem Durchlauf im NVS (Namespace `probe`, Blobs `rep_dev` und `rep_wait`,
Laufnummern `run_dev`/`run_wait`, Zähler `runs`, je höchstens 12 KB, bei Überlauf fallen die ältesten Zeilen weg).

1. **Flashen:** Board an den Mac, `idf.py -p /dev/cu.usbmodem* flash monitor`.
2. **Test:** Mac-Kabel ab, Board anderweitig mit Strom versorgen (Netzteil/Powerbank an der zweiten Buchse oder am 5V-Pin;
   vorher prüfen, dass die native Buchse dann 5 V liefert). Display an die native Buchse (OTG-Adapter C auf A + Kabel A auf C).
   Nach dem Start läuft 10 s ein Countdown (LED weiß/blau blinkend), danach startet der Host (LED wie in der Tabelle unten).
3. **Auslesen:** Display ab, Board wieder an den Mac, `idf.py -p /dev/cu.usbmodem* monitor`, ggf. RESET drücken.
   In den ersten 10 s erscheinen zuerst „Letzter Lauf MIT Gerät (Lauf N)“ (der Bericht vom Display, den Text kopieren)
   und danach kurz „Letzter Lauf ohne Gerät (Lauf M)“. Ist noch kein Lauf mit Gerät gespeichert, steht dort
   „Noch kein Lauf mit Gerät gespeichert.“

Es gibt zwei getrennte Plätze: `rep_dev` (letzter Lauf, in dem ein USB-Gerät erkannt wurde) und `rep_wait`
(letzter Lauf ohne Gerät). Der Bericht vom Display bleibt deshalb erhalten, auch wenn das Board danach ohne Display
am Mac läuft; ein Lauf ohne Gerät überschreibt nur `rep_wait`. Beim Warten ohne Gerät wird nur einmal pro Lauf
gespeichert (beim ersten 30-s-Hinweis). Ein alter Einzelbericht (Schlüssel `report`) wird beim Start einmal als
„Alter Bericht“ ausgegeben und dann gelöscht.
Wer am Mac im Log bleiben will, ohne dass der Host startet, hält beim Start (während des Countdowns) die BOOT-Taste
(GPIO 0, per `PROBE_BOOT_GPIO` änderbar). Dann werden beide Berichte alle 30 s erneut ausgegeben, der Host startet nicht
und die gespeicherten Berichte werden nicht überschrieben.

Die Komponenten `espressif/usb_host_msc` und `espressif/led_strip` lädt der Build aus der ESP Component Registry.
Die festgelegten Versionen stehen in `dependencies.lock`.

Die Partitionstabelle (`partitions.csv`) hat eine 64-KB-NVS für den Bericht.

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
nach außen ab, wenn das Board über den 5V-Pin oder die zweite Buchse versorgt wird. Je nach Revision muss dafür
ggf. auch die USB-OTG-Lötbrücke (Jumper) geschlossen werden. Das ist nicht gesichert: Vor dem Anstecken des Displays
mit dem Multimeter am Ende des Kabels nachmessen, ob dort 5 V anliegen. Siehe auch Schaltplan und Doku
der eigenen Board-Revision.

## LED-Farben

| Farbe        | Bedeutung |
|--------------|-----------|
| weiß/blau blinkend | Countdown vor dem Host-Start (10 s) |
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

## Bekanntes Problem: MPS 512

Das Display meldet bei Full Speed Bulk-Endpunkte mit wMaxPacketSize 512 (normwidrig, FS-Bulk max. 64). Der FIFO des
ESP32-S3 reicht dafür nicht, das Interface lässt sich nicht belegen:

```
I usb-probe: MSC-Gerät angesteckt (usb_addr=1)
E HCD DWC: EP MPS (512) exceeds supported limit (408)
E USBH: EP Alloc error: ESP_ERR_NOT_SUPPORTED
E USB HOST: Claiming interface error: ESP_ERR_NOT_SUPPORTED
```

Experiment (Branch `experiment/probe-mps64`): `espressif/usb` 1.6.0 liegt gepatcht unter `components/usb/`
(Details in `components/usb/PATCH.md`). Mit `CONFIG_USB_HOST_CLAMP_FS_BULK_MPS=y` wird die MPS für FS-Bulk-Endpunkte
auf 64 begrenzt – in der Hoffnung, dass das Gerät bei FS tatsächlich nur 64-Byte-Pakete sendet. Zusätzlich gibt die
Firmware nach dem Anstecken Geschwindigkeit, bcdUSB, VID/PID und alle Endpunkte (Adresse/Typ/MPS) aus.

Ergebnis Clamp IN+OUT auf 64: Enumeration und READ CAPACITY klappen, READ10 scheitert mit `Transfer failed: Status 5`
(OVERFLOW) – das Gerät sendet bei Full Speed tatsächlich 512-Byte-Pakete auf Bulk IN.

Neuer Versuch: Clamp nur noch für Bulk OUT (dort gehen nur 31-Byte-CBWs raus), Bulk IN behält MPS 512. Damit das
passt, setzt die Firmware eine eigene FIFO-Aufteilung (`fifo_settings_custom`, 200 Zeilen gesamt): RX 184 / NPTX 16 /
PTX 0 → MPS-Grenzen IN 728, NPTX 64, PTX 0 (Default war RX 104 / NPTX 64 / PTX 32 → IN 408). Die Grenzen stehen beim
Start im Log (`HCD DWC: FIFO lines: ...`). Noch nicht am Gerät getestet.
