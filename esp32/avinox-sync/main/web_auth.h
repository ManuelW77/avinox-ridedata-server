#pragma once

#include <stdbool.h>

/*
 * Reine Hilfsfunktionen für den Zugriffsschutz der Weboberfläche (ohne HTTP-Server, daher am Host testbar).
 */

typedef enum {
    AUTH_OK = 0,
    AUTH_NO_HEADER,         /* kein Authorization-Header */
    AUTH_TRUNCATED,         /* Header zu lang / abgeschnitten (vom Aufrufer gesetzt) */
    AUTH_NOT_BASIC,         /* Schema ist nicht "Basic" */
    AUTH_BAD_BASE64,        /* Base64 nicht dekodierbar oder Ergebnis zu lang */
    AUTH_NO_COLON,          /* kein Doppelpunkt zwischen Benutzer und Passwort */
    AUTH_BAD_USER,
    AUTH_BAD_PASS,
} web_auth_reason_t;

/* Diagnose zu einer Anmeldung. Enthält nie Passwortinhalt, nur Längen. */
typedef struct {
    web_auth_reason_t reason;
    char user[33];          /* empfangener Benutzername: nur druckbares ASCII, sonst '?', auf 32 Zeichen gekürzt */
    int pass_len;           /* Länge des empfangenen Passworts (-1 = unbekannt) */
    int expected_pass_len;
} web_auth_info_t;

/* Text zum Grund (für Logs). */
const char *web_auth_reason_str(web_auth_reason_t r);

/* Wie web_basic_auth_valid, liefert aber Grund, Benutzername und Längen in *info (darf NULL sein). */
bool web_basic_auth_check(const char *auth_hdr, const char *user, const char *pass, web_auth_info_t *info);

/* Wert des Authorization-Headers ("Basic <base64>") gegen Benutzer und Passwort prüfen.
 * Der Vergleich läuft zeitkonstant über die gesamte erwartete Länge. */
bool web_basic_auth_valid(const char *auth_hdr, const char *user, const char *pass);

/* CSRF-Schutz: Entspricht der Host-Teil von "Origin" (z. B. "http://192.168.1.5") dem Host-Header?
 * Ohne Port-Abweichung, Groß-/Kleinschreibung egal. "null" oder fehlender Host gilt als Abweichung. */
bool web_origin_matches_host(const char *origin, const char *host);

/* CSRF-Fallback ohne Origin: Entspricht der Host-Teil von "Referer" (Schema://host[:port]/Pfad) dem Host-Header? */
bool web_referer_matches_host(const char *referer, const char *host);
