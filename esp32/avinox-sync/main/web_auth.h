#pragma once

#include <stdbool.h>

/*
 * Reine Hilfsfunktionen für den Zugriffsschutz der Weboberfläche (ohne HTTP-Server, daher am Host testbar).
 */

/* Wert des Authorization-Headers ("Basic <base64>") gegen Benutzer und Passwort prüfen.
 * Der Vergleich läuft zeitkonstant über die gesamte erwartete Länge. */
bool web_basic_auth_valid(const char *auth_hdr, const char *user, const char *pass);

/* CSRF-Schutz: Entspricht der Host-Teil von "Origin" (z. B. "http://192.168.1.5") dem Host-Header?
 * Ohne Port-Abweichung, Groß-/Kleinschreibung egal. "null" oder fehlender Host gilt als Abweichung. */
bool web_origin_matches_host(const char *origin, const char *host);
