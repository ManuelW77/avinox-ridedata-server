#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#define USB_MNT_PATH "/usb"

typedef enum {
    DRIVE_ABSENT = 0,   /* kein Gerät */
    DRIVE_READY,        /* Gerät erkannt und MSC-Treiber angemeldet */
    DRIVE_FAILED,       /* Gerät da, aber Anmeldung gescheitert (bis zum Abziehen) */
} drive_state_t;

/* USB-Host + MSC-Treiber starten (eigene FIFO-Aufteilung, siehe usb_drive.c). */
esp_err_t usb_drive_start(void);
/* true, wenn der USB-Host gar nicht läuft (schwerer Fehler). */
bool usb_drive_host_failed(void);
drive_state_t usb_drive_state(void);
/* Zählt jedes neu angemeldete Laufwerk hoch. */
uint32_t usb_drive_generation(void);

/* Laufwerk unter USB_MNT_PATH einhängen (nur lesend benutzen, nie formatieren).
 * Hält bis usb_drive_unmount() eine Sperre, damit das Abziehen nicht mittendrin aufräumt. */
esp_err_t usb_drive_mount(void);
void usb_drive_unmount(void);
