# Lokale Kopie von espressif/usb

- Herkunft: Component Registry `espressif/usb` 1.6.0 (esp-usb, commit 630f1c4e, `host/usb`), Lizenz Apache-2.0 (siehe `LICENSE`).
- Eingebunden über `main/idf_component.yml` mit `override_path: "../components/usb"`; damit nutzt auch `usb_host_msc` diese Kopie.
- `CHECKSUMS.json` entfernt (Dateien sind verändert).

## Änderungen

1. `Kconfig`: neuer Schalter `CONFIG_USB_HOST_CLAMP_FS_BULK_MPS` (default y).
2. `src/hcd_dwc.c`: neue Funktion `pipe_effective_mps()`. Bei Gerätegeschwindigkeit Full Speed, Bulk-**OUT**-Endpunkt und
   wMaxPacketSize > 64 wird 64 verwendet – sowohl in `pipe_alloc_hcd_support_verification()` (FIFO-Limit-Prüfung,
   mit `ESP_LOGW "Bulk-EP 0x..: MPS 512 -> 64 (Full Speed)"`) als auch in `pipe_set_ep_char()` (`ep_char.mps`).
   Bulk IN behält die Deskriptor-MPS (512): erster Test mit Clamp auch für IN ergab READ10 mit Status 5 (OVERFLOW),
   das Gerät sendet bei FS echte 512-Byte-Pakete. Dafür setzt die Firmware eine eigene FIFO-Aufteilung (RX groß).
   Alle weiteren HCD-Pfade (Paketaufteilung, Transfergrößenlimit) lesen `ep_char.mps` und nutzen so automatisch 64.

3. `src/hcd_dwc.c` `hcd_port_init()`: FIFO-Log von `ESP_LOGD` auf `ESP_LOGI` angehoben und um Gesamtzeilen und
   resultierende MPS-Grenzen (IN/NPTX/PTX, Formel wie `usb_dwc_hal_get_mps_limits()`) erweitert.

## Geprüft, nicht geändert

- `usbh.c` `usbh_ep_enqueue_urb()`: Compliance-Prüfung nutzt weiter die Deskriptor-MPS (512): IN-Transfers müssen ein
  Vielfaches von 512 sein. Das ist strenger als nötig, aber jedes Vielfache von 512 ist auch eines von 64 – unschädlich.
- `usb_host_msc` (`msc_host.c`): rundet IN-Puffer mit `usb_round_up_to_mps(size, 512)` auf – Puffer werden eher größer, nie zu klein.
- `usb_host.c` `usb_round_up_to_mps` wird nur für EP0 (bMaxPacketSize0) genutzt.
