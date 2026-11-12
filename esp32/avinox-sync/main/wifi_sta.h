#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "config.h"

/* WLAN als Station starten. Ohne Verbindung nach 30 s: alle 60 s neuer Versuch. */
esp_err_t wifi_sta_start(const app_config_t *cfg);
bool wifi_sta_connected(void);
/* Zählt jede neue Verbindung (IP erhalten) hoch – für "Sync sofort nach Verbinden". */
uint32_t wifi_sta_generation(void);
