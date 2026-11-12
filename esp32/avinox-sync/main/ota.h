#pragma once

#include <stdbool.h>
#include "config.h"

/* Firmware-Update über den eigenen Server (GET /api/v1/firmware/latest, Download, Rollback). Ablauf: README. */

/* Beim Start, direkt nach dem NVS: erkennt Rollback und frisch installierte Firmware.
 * configured == false (Einrichtungsmodus): laufende Firmware sofort als gültig markieren. */
void ota_boot(bool configured);

/* Server hat erfolgreich geantwortet (Heartbeat/Bericht 2xx oder Manifest 200).
 * Bestätigt eine frisch installierte Firmware, wenn WLAN verbunden ist. */
void ota_server_ok(void);

/* Task: Bestätigungsfrist, einmaliger Rollback-Bericht, Prüfung alle CONFIG_AVX_OTA_CHECK_S. */
void ota_start(const app_config_t *cfg);

/* Sync-Schleife: um sync_run() klammern, damit keine Prüfung während eines Uploads läuft. */
void ota_sync_begin(void);
void ota_sync_end(void);

/* Nach einem abgeschlossenen Sync prüfen (aus der Sync-Schleife, nach ota_sync_end). */
void ota_check_now(void);
