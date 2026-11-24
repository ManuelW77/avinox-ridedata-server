#pragma once

#include <stdbool.h>
#include <stddef.h>
#include "config.h"
#include "esp_http_client.h"
#include "sync.h"

/* Geräte-ID "avinox-sync-XXXX" (letzte 2 Byte der STA-MAC), wird bei jeder Anfrage als X-Avinox-Device gesendet. */
const char *report_device_id(void);

/* POST /api/v1/device/report. res == NULL: Heartbeat, sonst Sync-Bericht. Fehler werden nur geloggt.
 * Antwort mit firmware_update löst ota_request_update() aus. */
void report_send(const app_config_t *cfg, const sync_result_t *res);

/* Heartbeat synchron senden (wartet bis 12 s auf einen laufenden Bericht). true bei HTTP 2xx.
 * Meldet der Server eine andere Firmware (firmware_update), wird ota_request_update() gesetzt. */
bool report_hello(const app_config_t *cfg);

/* OTA-Bericht (event "ota"): result "started" | "ok" | "failed" | "rolled_back", error optional.
 * true bei HTTP 2xx. */
bool report_send_ota(const app_config_t *cfg, const char *from, const char *to, const char *result, const char *error);

/* Task: Heartbeat sofort nach jeder WLAN-Verbindung und dann alle CONFIG_AVX_HEARTBEAT_S Sekunden. */
void report_start_heartbeat(const app_config_t *cfg);

/* Setzt die Header, die jede Anfrage an den Server trägt: Authorization (Bearer) und X-Avinox-Device. */
void report_set_headers(esp_http_client_handle_t c, const char *token);

/* Prüft ohne Speichern, ob Server und Token funktionieren: GET <base_url>/api/v1/manifest mit denselben Headern
 * wie der Sync, Antwort wird verworfen. Liefert den HTTP-Status oder -1 bei Verbindungsfehler/Zeitüberschreitung. */
int report_probe_manifest(const char *base_url, const char *token, int timeout_ms);
