#pragma once

#include <stddef.h>
#include "config.h"
#include "sync.h"

/* Geräte-ID "avinox-sync-XXXX" (letzte 2 Byte der STA-MAC), wird bei jeder Anfrage als X-Avinox-Device gesendet. */
const char *report_device_id(void);

/* POST /api/v1/device/report. res == NULL: Heartbeat, sonst Sync-Bericht. Fehler werden nur geloggt. */
void report_send(const app_config_t *cfg, const sync_result_t *res);

/* Task: Heartbeat sofort nach jeder WLAN-Verbindung und dann alle CONFIG_AVX_HEARTBEAT_S Sekunden. */
void report_start_heartbeat(const app_config_t *cfg);
