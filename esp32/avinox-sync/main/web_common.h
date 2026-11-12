#pragma once

/*
 * Gemeinsamer Teil von Einrichtungsportal (portal.c) und Betriebs-Weboberfläche (webui.c):
 * Textpuffer, HTML-Escaping, Formular-Parsing, DE/EN-Texte, Seitengerüst, Einstellungsformular
 * und das Speichern samt Validierung.
 */

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"
#include "esp_http_server.h"
#include "esp_wifi_types.h"
#include "config.h"

/* ---- Textpuffer (wächst bei Bedarf, wird in einem Stück gesendet) */

typedef struct {
    char *p;
    size_t len, cap;
    bool oom;
} buf_t;

void buf_init(buf_t *b);
void buf_append(buf_t *b, const char *s);
void buf_append_esc(buf_t *b, const char *s);
/* Sendet den Puffer als HTML in EINEM Stück und gibt ihn frei. */
esp_err_t buf_send(httpd_req_t *req, buf_t *b);

/* ---- Hilfsfunktionen */

/* Wert eines Feldes aus application/x-www-form-urlencoded lesen und dekodieren; false = zu lang. */
bool web_form_get(const char *body, const char *key, char *out, size_t cap);
void web_trim(char *s);
/* Neustart in 2 s (damit die Antwort noch rausgeht). */
void web_schedule_restart(void);

/* ---- Sprache (DE/EN) */

typedef enum {
    T_STATUS, T_WIFI, T_WIFI_PASS, T_SERVER, T_TOKEN, T_LAST_SYNC, T_NOT_SET, T_SET, T_NONE_OPEN, T_MISSING,
    T_NONE_YET, T_SETUP, T_WIFI_LIST, T_PICK, T_RESCAN, T_RESCAN_NOTE, T_PASS_LBL, T_PH_SET_KEEP, T_OPEN_NET,
    T_SSID_LBL, T_SSID_NOTE, T_URL_LBL, T_TOKEN_LBL, T_PH_TOKEN, T_AP_H, T_AP_NEW, T_PH_REQUIRED, T_AP_REPEAT,
    T_AP_NOTE, T_SAVE_BTN, T_RESTART_BTN, T_OPEN_SUFFIX, T_BACK_FORM, T_TO_SETUP, T_CAPTIVE,
    T_E_LONG, T_E_NOSSID, T_E_PASSLEN, T_E_APPASS, T_E_APMATCH, T_E_URL, T_E_TOKEN, T_E_FLASH, T_E_FORM,
    T_OK_SAVED, T_OK_RESTART, T_E_MEM,
    /* nur Betriebs-Weboberfläche */
    T_DEVICE, T_FIRMWARE, T_IP, T_SIGNAL, T_UPTIME, T_HEAP, T_USB, T_USB_ABSENT, T_USB_READY, T_USB_FAILED,
    T_USB_HOSTFAIL, T_NOT_CONNECTED, T_SETTINGS, T_ACCESS_H, T_ACCESS_NEW, T_ACCESS_NOTE, T_WARN_WIFI,
    T_SETUP_BTN, T_SETUP_NOTE, T_OK_SAVED_WEB, T_OK_SETUP,
    T_LANG_H, T_LANG_NOTE, T_LANG_SAVE,
    T_COUNT
} web_text_t;

/* Sprache der Anfrage bestimmen: ?lang= (setzt Cookie) > Cookie > Accept-Language > de. */
void web_detect_lang(httpd_req_t *req);
int web_lang(void);                     /* 0 = de, 1 = en */
const char *web_t(web_text_t id);
#define T(id) web_t(id)

/* ---- Seitengerüst */

/* Seitenkopf samt Sprachumschalter; path = aktuelle Seite für die ?lang=-Links. */
void web_page_head(buf_t *b, const char *path);
/* Seitenende mit Footer und Firmware-Version. */
void web_page_tail(buf_t *b);
/* Meldungsseite; cls = "ok" | "err". */
esp_err_t web_send_message(httpd_req_t *req, const char *cls, web_text_t text, bool back_link);

/* ---- Einstellungsformular */

/* Einstellungen als <section> samt "Neu starten"-Knopf. portal = Einrichtungsmodus (SoftAP), sonst Betrieb. */
void web_render_form(buf_t *b, const app_config_t *cfg, const wifi_ap_record_t *aps, uint16_t ap_count, bool portal);
/* POST /save: Formular lesen, prüfen, speichern, antworten, Neustart planen. Bei Erfolg wird *cur aktualisiert. */
esp_err_t web_handle_save(httpd_req_t *req, app_config_t *cur, bool portal);
/* WLAN-Suche (blockierend). Liefert die Anzahl Netze oder -1 bei Fehler. */
int web_scan(wifi_ap_record_t *aps, uint16_t max);
