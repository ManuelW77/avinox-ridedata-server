#pragma once

#include "config.h"

/*
 * Einrichtungsmodus: SoftAP "Avinox-Sync-XXXX", HTTP-Server auf 192.168.4.1 mit Formular
 * (SSID, WLAN-Passwort, Server-URL, Token) und Statusseite, Captive-Portal-DNS.
 * Speichern -> NVS -> Neustart. Kehrt nie zurück.
 * rescue = Notfallmodus (BOOT-Taste): SoftAP OFFEN, neues Zugangspasswort Pflicht, nach 15 min ohne Speichern
 * Neustart in den Normalbetrieb (nur bei vollständiger Konfiguration).
 */
void portal_run(bool rescue);
