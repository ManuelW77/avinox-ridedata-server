#pragma once

#include "config.h"

/*
 * Weboberfläche im Normalbetrieb (Gerät ist Station im Heim-WLAN), Port 80.
 * Zugriff nur mit HTTP Basic Auth: Benutzer "admin", Passwort = Einrichtungs-Passwort (cfg->ap_pass).
 * Ohne Passwort (mindestens 8 Zeichen) startet keine Oberfläche. Nach wifi_sta_start() aufrufen.
 */
void webui_start(const app_config_t *cfg);
