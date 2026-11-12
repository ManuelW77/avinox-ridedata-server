#pragma once

#include <stdbool.h>
#include "esp_err.h"

#define CFG_SSID_MAX   32
#define CFG_PASS_MAX   64
#define CFG_URL_MAX    200
#define CFG_TOKEN_MAX  128

/* Konfiguration im NVS (Namespace "cfg"). Passwort und Token nie loggen. */
typedef struct {
    char ssid[CFG_SSID_MAX + 1];
    char pass[CFG_PASS_MAX + 1];
    char url[CFG_URL_MAX + 1];      /* ohne abschließenden '/' */
    char token[CFG_TOKEN_MAX + 1];
    char ap_pass[CFG_PASS_MAX + 1]; /* WPA2-Passwort des Einrichtungs-WLANs, leer = offen */
} app_config_t;

/* NVS-Flash initialisieren (bei Bedarf löschen und neu anlegen). */
esp_err_t config_nvs_init(void);

/* Lädt die Konfiguration; fehlende Schlüssel bleiben leer. */
esp_err_t config_load(app_config_t *cfg);

/* SSID, Server-URL und Token gesetzt? (WLAN-Passwort darf leer sein = offenes WLAN) */
bool config_complete(const app_config_t *cfg);

esp_err_t config_save(const app_config_t *cfg);

/* Werkseinstellung: gesamten Namespace "cfg" löschen. */
esp_err_t config_factory_reset(void);

/* Merker für "beim nächsten Start Einrichtungsmodus". rescue = per BOOT-Taste ausgelöst (Notfallmodus,
 * Einrichtungs-WLAN offen), sonst über die Weboberfläche (normal). */
esp_err_t config_request_portal(bool rescue);

typedef enum {
    PORTAL_REQ_NONE = 0,
    PORTAL_REQ_NORMAL = 1,
    PORTAL_REQ_RESCUE = 2,
} portal_request_t;

/* Liefert den Merker und löscht ihn dabei. */
portal_request_t config_take_portal_request(void);

/* Server-URL säubern (Leerzeichen, abschließende '/'); true wenn http:// oder https://. */
bool config_normalize_url(char *url);
