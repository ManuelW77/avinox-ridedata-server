#pragma once

#include "config.h"

/*
 * Einrichtungsmodus: SoftAP "Avinox-Sync-XXXX", HTTP-Server auf 192.168.4.1 mit Formular
 * (SSID, WLAN-Passwort, Server-URL, Token) und Statusseite, Captive-Portal-DNS.
 * Speichern -> NVS -> Neustart. Kehrt nie zurück.
 */
void portal_run(void);
