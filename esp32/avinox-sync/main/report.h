#pragma once

#include <stdbool.h>
#include <stddef.h>
#include "config.h"
#include "sync.h"

/* Geräte-ID "avinox-sync-XXXX" (letzte 2 Byte der STA-MAC), wird bei jeder Anfrage als X-Avinox-Device gesendet. */
const char *report_device_id(void);

/* POST /api/v1/device/report. res == NULL: Heartbeat, sonst Sync-Bericht. Fehler werden nur geloggt. */
void report_send(const app_config_t *cfg, const sync_result_t *res);

/* OTA-Bericht (event "ota"): result "started" | "ok" | "failed" | "rolled_back", error optional.
 * true bei HTTP 2xx. */
bool report_send_ota(const app_config_t *cfg, const char *from, const char *to, const char *result, const char *error);

/* Task: Heartbeat sofort nach jeder WLAN-Verbindung und dann alle CONFIG_AVX_HEARTBEAT_S Sekunden. */
void report_start_heartbeat(const app_config_t *cfg);
