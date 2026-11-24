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

/* Task: Bestätigungsfrist, einmaliger Rollback-Bericht, Update bei Hinweis des Servers. */
void ota_start(const app_config_t *cfg);

/* Der Server hat in einer Antwort eine andere Firmware gemeldet (firmware_update). */
void ota_request_update(void);

/* Sync-Schleife: um sync_run() klammern, damit keine Prüfung während eines Uploads läuft. */
void ota_sync_begin(void);
void ota_sync_end(void);

/* Vor jedem Auslesen (zwischen ota_sync_begin und sync_run): Hallo an den Server; meldet er eine neue
   Version, wird sie geladen und neu gestartet. false = Server nicht erreichbar: dann nicht auslesen. */
bool ota_check_before_sync(void);
