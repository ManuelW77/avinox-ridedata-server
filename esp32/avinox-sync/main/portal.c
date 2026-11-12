#include "portal.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_http_server.h"
#include "lwip/sockets.h"

#include "led.h"
#include "sync.h"

static const char *TAG = "portal";

#define AP_IP_STR       "192.168.4.1"
#define MAX_APS         20
#define FORM_MAX        1536

static app_config_t s_cfg;
static char s_ap_ssid[33];
static wifi_ap_record_t s_aps[MAX_APS];
static uint16_t s_ap_count;

/* ------------------------------------------------------------ Hilfsfunktionen */

typedef struct {
    char *p;
    size_t len, cap;
    bool oom;
} buf_t;

static void buf_init(buf_t *b)
{
    b->len = 0;
    b->cap = 8192;
    b->oom = false;
    b->p = malloc(b->cap);
    if (!b->p) {
        b->cap = 0;
        b->oom = true;
    } else {
        b->p[0] = '\0';
    }
}

static bool buf_reserve(buf_t *b, size_t extra)
{
    if (b->oom) {
        return false;
    }
    if (b->len + extra + 1 <= b->cap) {
        return true;
    }
    size_t ncap = b->cap;
    while (b->len + extra + 1 > ncap) {
        ncap *= 2;
    }
    char *np = realloc(b->p, ncap);
    if (!np) {
        b->oom = true;
        return false;
    }
    b->p = np;
    b->cap = ncap;
    return true;
}

static void buf_append(buf_t *b, const char *s)
{
    size_t n = strlen(s);
    if (!buf_reserve(b, n)) {
        return;
    }
    memcpy(b->p + b->len, s, n + 1);
    b->len += n;
}

static void buf_append_esc(buf_t *b, const char *s)
{
    for (; *s; s++) {
        const char *rep = NULL;
        switch (*s) {
        case '&': rep = "&amp;"; break;
        case '<': rep = "&lt;"; break;
        case '>': rep = "&gt;"; break;
        case '"': rep = "&quot;"; break;
        case '\'': rep = "&#39;"; break;
        default: break;
        }
        if (rep) {
            buf_append(b, rep);
        } else {
            if (!buf_reserve(b, 1)) {
                return;
            }
            b->p[b->len++] = *s;
            b->p[b->len] = '\0';
        }
    }
}

/* Sendet den Puffer in EINEM Stück und gibt ihn frei. */
static esp_err_t buf_send(httpd_req_t *req, buf_t *b)
{
    esp_err_t err;
    if (b->oom) {
        err = httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    } else {
        httpd_resp_set_type(req, "text/html; charset=utf-8");
        httpd_resp_set_hdr(req, "Cache-Control", "no-store");
        err = httpd_resp_send(req, b->p, b->len);
    }
    free(b->p);
    b->p = NULL;
    b->len = b->cap = 0;
    return err;
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Wert eines Feldes aus application/x-www-form-urlencoded lesen und dekodieren. */
static bool form_get(const char *body, const char *key, char *out, size_t cap)
{
    size_t klen = strlen(key);
    const char *p = body;
    while (p && *p) {
        const char *end = strchr(p, '&');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        if (len > klen && strncmp(p, key, klen) == 0 && p[klen] == '=') {
            size_t o = 0;
            for (size_t i = klen + 1; i < len; i++) {
                char c = p[i];
                if (c == '+') {
                    c = ' ';
                } else if (c == '%' && i + 2 < len && hexval(p[i + 1]) >= 0 && hexval(p[i + 2]) >= 0) {
                    c = (char)(hexval(p[i + 1]) * 16 + hexval(p[i + 2]));
                    i += 2;
                }
                if (o + 1 >= cap) {
                    out[o] = '\0';
                    return false;       /* zu lang */
                }
                out[o++] = c;
            }
            out[o] = '\0';
            return true;
        }
        p = end ? end + 1 : NULL;
    }
    out[0] = '\0';
    return true;
}

static void trim(char *s)
{
    size_t start = 0, n = strlen(s);
    while (start < n && isspace((unsigned char)s[start])) start++;
    while (n > start && isspace((unsigned char)s[n - 1])) n--;
    memmove(s, s + start, n - start);
    s[n - start] = '\0';
}

static void restart_cb(void *arg)
{
    esp_restart();
}

static void schedule_restart(void)
{
    const esp_timer_create_args_t args = { .callback = restart_cb, .name = "restart" };
    esp_timer_handle_t t;
    if (esp_timer_create(&args, &t) == ESP_OK && esp_timer_start_once(t, 2000 * 1000) == ESP_OK) {
        return;
    }
    esp_restart();
}

/* ------------------------------------------------------------ Sprache (DE/EN) */

enum {
    T_STATUS, T_WIFI, T_WIFI_PASS, T_SERVER, T_TOKEN, T_LAST_SYNC, T_NOT_SET, T_SET, T_NONE_OPEN, T_MISSING,
    T_NONE_YET, T_SETUP, T_WIFI_LIST, T_PICK, T_RESCAN, T_RESCAN_NOTE, T_PASS_LBL, T_PH_SET_KEEP, T_OPEN_NET,
    T_SSID_LBL, T_SSID_NOTE, T_URL_LBL, T_TOKEN_LBL, T_PH_TOKEN, T_AP_H, T_AP_NEW, T_PH_REQUIRED, T_AP_REPEAT,
    T_AP_NOTE, T_SAVE_BTN, T_RESTART_BTN, T_OPEN_SUFFIX, T_BACK_FORM, T_TO_SETUP, T_CAPTIVE,
    T_E_LONG, T_E_NOSSID, T_E_PASSLEN, T_E_APPASS, T_E_APMATCH, T_E_URL, T_E_TOKEN, T_E_FLASH, T_E_FORM,
    T_OK_SAVED, T_OK_RESTART, T_E_MEM
};

static const char *const TXT[][2] = {
    [T_STATUS] = { "Status", "Status" },
    [T_WIFI] = { "WLAN", "Wi-Fi" },
    [T_WIFI_PASS] = { "WLAN-Passwort", "Wi-Fi password" },
    [T_SERVER] = { "Server", "Server" },
    [T_TOKEN] = { "Token", "Token" },
    [T_LAST_SYNC] = { "Letzter Sync", "Last sync" },
    [T_NOT_SET] = { "nicht eingerichtet", "not configured" },
    [T_SET] = { "gesetzt", "set" },
    [T_NONE_OPEN] = { "keins (offenes WLAN)", "none (open network)" },
    [T_MISSING] = { "fehlt", "missing" },
    [T_NONE_YET] = { "noch keiner", "none yet" },
    [T_SETUP] = { "Einrichtung", "Setup" },
    [T_WIFI_LIST] = { "WLAN aus der Liste", "Wi-Fi from the list" },
    [T_PICK] = { "– auswählen –", "– select –" },
    [T_RESCAN] = { "Netze neu suchen", "Rescan networks" },
    [T_RESCAN_NOTE] = { " (die Verbindung zum Handy kann kurz abreißen)", " (the connection to your phone may drop briefly)" },
    [T_PASS_LBL] = { "WLAN-Passwort", "Wi-Fi password" },
    [T_PH_SET_KEEP] = { "gesetzt – leer lassen = unverändert", "set – leave empty to keep" },
    [T_OPEN_NET] = { "Offenes WLAN (kein Passwort)", "Open network (no password)" },
    [T_SSID_LBL] = { "WLAN-Name (SSID)", "Wi-Fi name (SSID)" },
    [T_SSID_NOTE] = { "Freitext hat Vorrang vor der Auswahl.", "Typed text takes precedence over the selection." },
    [T_URL_LBL] = { "Server-URL", "Server URL" },
    [T_TOKEN_LBL] = { "Upload-Token", "Upload token" },
    [T_PH_TOKEN] = { "vom Server, siehe README", "from the server, see README" },
    [T_AP_H] = { "Passwort für dieses Einrichtungs-WLAN", "Password for this setup Wi-Fi" },
    [T_AP_NEW] = { "Neues Passwort (mindestens 8 Zeichen)", "New password (at least 8 characters)" },
    [T_PH_REQUIRED] = { "Pflicht", "required" },
    [T_AP_REPEAT] = { "Passwort wiederholen", "Repeat password" },
    [T_AP_NOTE] = { "Gilt ab dem nächsten Einrichtungsmodus (BOOT 3 s).", "Applies from the next setup mode (hold BOOT 3 s)." },
    [T_SAVE_BTN] = { "Speichern und neu starten", "Save and restart" },
    [T_RESTART_BTN] = { "Ohne Änderung neu starten", "Restart without changes" },
    [T_OPEN_SUFFIX] = { ", offen", ", open" },
    [T_BACK_FORM] = { "Zurück zum Formular", "Back to the form" },
    [T_TO_SETUP] = { "Zur Einrichtung", "To setup" },
    [T_CAPTIVE] = { "Einrichtung öffnen", "Open setup" },
    [T_E_LONG] = { "Ein Feld ist zu lang.", "A field is too long." },
    [T_E_NOSSID] = { "Bitte ein WLAN auswählen oder den Namen eintragen.", "Please select a network or enter its name." },
    [T_E_PASSLEN] = { "Das WLAN-Passwort muss mindestens 8 Zeichen haben.", "The Wi-Fi password must have at least 8 characters." },
    [T_E_APPASS] = { "Bitte ein Passwort für das Einrichtungs-WLAN vergeben (mindestens 8 Zeichen).",
                     "Please set a password for the setup Wi-Fi (at least 8 characters)." },
    [T_E_APMATCH] = { "Die beiden Passwörter für das Einrichtungs-WLAN stimmen nicht überein.",
                      "The two passwords for the setup Wi-Fi do not match." },
    [T_E_URL] = { "Die Server-URL muss mit http:// oder https:// beginnen.", "The server URL must start with http:// or https://." },
    [T_E_TOKEN] = { "Bitte den Upload-Token eintragen.", "Please enter the upload token." },
    [T_E_FLASH] = { "Speichern im Flash fehlgeschlagen.", "Saving to flash failed." },
    [T_E_FORM] = { "Formular leer oder zu groß.", "Form empty or too large." },
    [T_OK_SAVED] = { "Gespeichert. Das Gerät startet neu und verbindet sich mit dem WLAN. Dieses Einrichtungs-WLAN verschwindet danach.",
                     "Saved. The device restarts and connects to the Wi-Fi. This setup Wi-Fi disappears afterwards." },
    [T_OK_RESTART] = { "Neustart ohne Änderung.", "Restarting without changes." },
    [T_E_MEM] = { "Zu wenig Speicher", "Out of memory" },
};

static int s_lang;      /* 0 = de, 1 = en; je Anfrage gesetzt (httpd arbeitet Anfragen nacheinander ab) */
#define T(id) TXT[id][s_lang]

static const char COOKIE_DE[] = "lang=de; Max-Age=31536000; Path=/; SameSite=Lax";
static const char COOKIE_EN[] = "lang=en; Max-Age=31536000; Path=/; SameSite=Lax";

static int lang_from_code(const char *v)
{
    if (strncmp(v, "en", 2) == 0) {
        return 1;
    }
    if (strncmp(v, "de", 2) == 0) {
        return 0;
    }
    return -1;
}

/* Sprache der Anfrage bestimmen: ?lang= (setzt Cookie) > Cookie > Accept-Language > de. */
static void detect_lang(httpd_req_t *req)
{
    char val[192];
    s_lang = 0;
    if (httpd_req_get_url_query_str(req, val, sizeof(val)) == ESP_OK) {
        char q[8];
        if (httpd_query_key_value(val, "lang", q, sizeof(q)) == ESP_OK) {
            int l = lang_from_code(q);
            if (l >= 0 && q[2] == '\0') {
                s_lang = l;
                httpd_resp_set_hdr(req, "Set-Cookie", l ? COOKIE_EN : COOKIE_DE);
                return;
            }
        }
    }
    if (httpd_req_get_hdr_value_str(req, "Cookie", val, sizeof(val)) == ESP_OK) {
        for (const char *c = val; c; ) {
            while (*c == ' ') {
                c++;
            }
            if (strncmp(c, "lang=", 5) == 0) {
                int l = lang_from_code(c + 5);
                if (l >= 0) {
                    s_lang = l;
                    return;
                }
            }
            c = strchr(c, ';');
            if (c) {
                c++;
            }
        }
    }
    char al[64];
    esp_err_t e = httpd_req_get_hdr_value_str(req, "Accept-Language", al, sizeof(al));
    if (e == ESP_OK || e == ESP_ERR_HTTPD_RESULT_TRUNC) {
        const char *a = al;
        while (*a == ' ') {
            a++;
        }
        s_lang = (strncasecmp(a, "en", 2) == 0);
    }
}

/* ------------------------------------------------------------ Seiten */

static const char PAGE_HEAD1[] = "<!DOCTYPE html><html lang=\"";
static const char PAGE_HEAD2[] = "\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>Avinox-Sync</title><style>"
    "body{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#f3f4f6;color:#111}"
    "main{max-width:32rem;margin:0 auto;padding:1rem}"
    "h1{font-size:1.4rem;margin:.5rem 0 1rem}h2{font-size:1.05rem;margin:0 0 .6rem}"
    "section{background:#fff;border-radius:10px;padding:1rem;margin-bottom:1rem;box-shadow:0 1px 2px #0002}"
    "label{display:block;font-weight:600;margin:.8rem 0 .25rem}"
    "input,select{box-sizing:border-box;width:100%;padding:.55rem;font-size:1rem;border:1px solid #bbb;border-radius:6px}"
    "input[type=checkbox]{width:auto;margin-right:.4rem}.cb{font-weight:400}"
    "button{margin-top:1rem;width:100%;padding:.7rem;font-size:1rem;border:0;border-radius:6px;background:#0a66c2;color:#fff}"
    "button.sec{background:#6b7280}small,.hint{color:#555;font-size:.85rem}"
    "dl{display:grid;grid-template-columns:max-content 1fr;gap:.3rem .8rem;margin:0}dt{color:#555}dd{margin:0;word-break:break-all}"
    ".err{background:#fee2e2;color:#7f1d1d}.ok{background:#dcfce7;color:#14532d}"
    "a{color:#0a66c2}"
    "footer{font-size:.75rem;color:#777;text-align:center;margin:1.2rem 0 .5rem}footer a{color:#777}"
    ".lang{text-align:right;font-size:.85rem;margin:0}"
    "</style></head><body><main>";

static const char PAGE_TAIL[] =
    "<footer>© 2026 Manuel Weiser · Build with <span role=\"img\" aria-label=\"Herz\">❤️</span> and a <span role=\"img\" aria-label=\"Fahrrad\">🚲</span> by Manuel Weiser in Leipzig · "
    "<a href=\"https://manuelw.de\">manuelw.de</a>";
static const char PAGE_TAIL_END[] = "</footer></main></body></html>";

/* Seitenende; Firmware-Version (aus PROJECT_VER) im Footer, HTML-escaped. */
static void page_tail(buf_t *b)
{
    buf_append(b, PAGE_TAIL);
    buf_append(b, " · Firmware v");
    buf_append_esc(b, esp_app_get_description()->version);
    buf_append(b, PAGE_TAIL_END);
}

/* Seitenkopf samt Sprachumschalter; path = aktuelle Seite für die ?lang=-Links. */
static void page_head(buf_t *b, const char *path)
{
    buf_append(b, PAGE_HEAD1);
    buf_append(b, s_lang ? "en" : "de");
    buf_append(b, PAGE_HEAD2);
    buf_append(b, "<p class=\"lang\"><a href=\"");
    buf_append(b, path);
    buf_append(b, "?lang=de\">DE</a> | <a href=\"");
    buf_append(b, path);
    buf_append(b, "?lang=en\">EN</a></p><h1>Avinox-Sync</h1>");
}

static void status_block(buf_t *b)
{
    buf_append(b, "<section><h2>");
    buf_append(b, T(T_STATUS));
    buf_append(b, "</h2><dl><dt>");
    buf_append(b, T(T_WIFI));
    buf_append(b, "</dt><dd>");
    if (s_cfg.ssid[0]) {
        buf_append_esc(b, s_cfg.ssid);
    } else {
        buf_append(b, "<i>");
        buf_append(b, T(T_NOT_SET));
        buf_append(b, "</i>");
    }
    buf_append(b, "</dd><dt>");
    buf_append(b, T(T_WIFI_PASS));
    buf_append(b, "</dt><dd>");
    buf_append(b, s_cfg.pass[0] ? T(T_SET) : T(T_NONE_OPEN));
    buf_append(b, "</dd><dt>");
    buf_append(b, T(T_SERVER));
    buf_append(b, "</dt><dd>");
    if (s_cfg.url[0]) {
        buf_append_esc(b, s_cfg.url);
    } else {
        buf_append(b, "<i>");
        buf_append(b, T(T_MISSING));
        buf_append(b, "</i>");
    }
    buf_append(b, "</dd><dt>");
    buf_append(b, T(T_TOKEN));
    buf_append(b, "</dt><dd>");
    if (s_cfg.token[0]) {
        buf_append(b, T(T_SET));
    } else {
        buf_append(b, "<i>");
        buf_append(b, T(T_MISSING));
        buf_append(b, "</i>");
    }
    buf_append(b, "</dd><dt>");
    buf_append(b, T(T_LAST_SYNC));
    buf_append(b, "</dt><dd>");
    sync_result_t r;
    if (sync_result_load(&r)) {
        char line[256];
        sync_result_format(&r, line, sizeof(line));
        buf_append_esc(b, line);
    } else {
        buf_append(b, T(T_NONE_YET));
    }
    buf_append(b, "</dd></dl></section>");
}

static esp_err_t send_message(httpd_req_t *req, const char *cls, int text, bool back_link)
{
    detect_lang(req);
    buf_t b;
    buf_init(&b);
    page_head(&b, "/");
    buf_append(&b, "<section class=\"");
    buf_append(&b, cls);
    buf_append(&b, "\"><p>");
    buf_append_esc(&b, T(text));
    buf_append(&b, "</p>");
    if (back_link) {
        buf_append(&b, "<p><a href=\"/\">");
        buf_append(&b, T(T_BACK_FORM));
        buf_append(&b, "</a></p>");
    }
    buf_append(&b, "</section>");
    page_tail(&b);
    return buf_send(req, &b);
}

static esp_err_t root_get(httpd_req_t *req)
{
    detect_lang(req);
    buf_t b;
    buf_init(&b);
    page_head(&b, "/");
    status_block(&b);

    buf_append(&b, "<section><h2>");
    buf_append(&b, T(T_SETUP));
    buf_append(&b, "</h2><form method=\"post\" action=\"/save\"><label for=\"sel\">");
    buf_append(&b, T(T_WIFI_LIST));
    buf_append(&b,
        "</label><select id=\"sel\" name=\"ssid_sel\" "
        "onchange=\"if(this.value)document.getElementById('ssid').value=this.value\">"
        "<option value=\"\">");
    buf_append(&b, T(T_PICK));
    buf_append(&b, "</option>");
    for (int i = 0; i < s_ap_count; i++) {
        const char *ssid = (const char *)s_aps[i].ssid;
        if (!ssid[0]) {
            continue;
        }
        bool dup = false;
        for (int j = 0; j < i; j++) {
            if (strcmp(ssid, (const char *)s_aps[j].ssid) == 0) {
                dup = true;
                break;
            }
        }
        if (dup) {
            continue;
        }
        char info[48];
        snprintf(info, sizeof(info), " (%d dBm%s)", s_aps[i].rssi, s_aps[i].authmode == WIFI_AUTH_OPEN ? T(T_OPEN_SUFFIX) : "");
        buf_append(&b, "<option value=\"");
        buf_append_esc(&b, ssid);
        buf_append(&b, "\">");
        buf_append_esc(&b, ssid);
        buf_append(&b, info);
        buf_append(&b, "</option>");
    }
    buf_append(&b, "</select><small><a href=\"/scan\">");
    buf_append(&b, T(T_RESCAN));
    buf_append(&b, "</a>");
    buf_append(&b, T(T_RESCAN_NOTE));
    buf_append(&b, "</small><label for=\"pass\">");
    buf_append(&b, T(T_PASS_LBL));
    buf_append(&b, "</label>"
        "<input id=\"pass\" name=\"pass\" type=\"password\" maxlength=\"63\" autocomplete=\"off\" placeholder=\"");
    buf_append(&b, s_cfg.pass[0] ? T(T_PH_SET_KEEP) : "");
    buf_append(&b, "\"><label class=\"cb\"><input type=\"checkbox\" name=\"open\" value=\"1\">");
    buf_append(&b, T(T_OPEN_NET));
    buf_append(&b, "</label><label for=\"ssid\">");
    buf_append(&b, T(T_SSID_LBL));
    buf_append(&b, "</label>"
        "<input id=\"ssid\" name=\"ssid\" maxlength=\"32\" autocomplete=\"off\" autocapitalize=\"none\" value=\"");
    buf_append_esc(&b, s_cfg.ssid);
    buf_append(&b, "\"><small>");
    buf_append(&b, T(T_SSID_NOTE));
    buf_append(&b, "</small><label for=\"url\">");
    buf_append(&b, T(T_URL_LBL));
    buf_append(&b, "</label>"
        "<input id=\"url\" name=\"url\" type=\"url\" maxlength=\"200\" autocapitalize=\"none\" "
        "placeholder=\"http://192.168.1.10:8000\" value=\"");
    buf_append_esc(&b, s_cfg.url);
    buf_append(&b, "\"><label for=\"token\">");
    buf_append(&b, T(T_TOKEN_LBL));
    buf_append(&b, "</label>"
        "<input id=\"token\" name=\"token\" type=\"password\" maxlength=\"128\" autocomplete=\"off\" placeholder=\"");
    buf_append(&b, s_cfg.token[0] ? T(T_PH_SET_KEEP) : T(T_PH_TOKEN));
    buf_append(&b, "\"><h2 style=\"margin-top:1.2rem\">");
    buf_append(&b, T(T_AP_H));
    buf_append(&b, "</h2><label for=\"appass\">");
    buf_append(&b, T(T_AP_NEW));
    buf_append(&b, "</label>"
        "<input id=\"appass\" name=\"ap_pass\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\" placeholder=\"");
    buf_append(&b, s_cfg.ap_pass[0] ? T(T_PH_SET_KEEP) : T(T_PH_REQUIRED));
    buf_append(&b, "\"><label for=\"appass2\">");
    buf_append(&b, T(T_AP_REPEAT));
    buf_append(&b, "</label>"
        "<input id=\"appass2\" name=\"ap_pass2\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\">"
        "<small>");
    buf_append(&b, T(T_AP_NOTE));
    buf_append(&b, "</small><button type=\"submit\">");
    buf_append(&b, T(T_SAVE_BTN));
    buf_append(&b, "</button></form><form method=\"post\" action=\"/restart\"><button class=\"sec\" type=\"submit\">");
    buf_append(&b, T(T_RESTART_BTN));
    buf_append(&b, "</button></form></section>");
    page_tail(&b);
    return buf_send(req, &b);
}

static esp_err_t status_get(httpd_req_t *req)
{
    detect_lang(req);
    buf_t b;
    buf_init(&b);
    page_head(&b, "/status");
    status_block(&b);
    buf_append(&b, "<p><a href=\"/\">");
    buf_append(&b, T(T_TO_SETUP));
    buf_append(&b, "</a></p>");
    page_tail(&b);
    return buf_send(req, &b);
}

static void do_scan(void)
{
    wifi_scan_config_t sc = { .show_hidden = false };
    esp_err_t err = esp_wifi_scan_start(&sc, true);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "WLAN-Suche: %s", esp_err_to_name(err));
        return;
    }
    uint16_t n = MAX_APS;
    if (esp_wifi_scan_get_ap_records(&n, s_aps) == ESP_OK) {
        s_ap_count = n;
    }
    ESP_LOGI(TAG, "WLAN-Suche: %u Netze", s_ap_count);
}

static esp_err_t scan_get(httpd_req_t *req)
{
    do_scan();
    httpd_resp_set_status(req, "303 See Other");
    httpd_resp_set_hdr(req, "Location", "/");
    return httpd_resp_send(req, NULL, 0);
}

/* Prüft die Formularwerte, speichert und antwortet. Aufrufer nullt die Passwortpuffer. */
static esp_err_t save_apply(httpd_req_t *req, app_config_t *ncp, bool fit, const char *ssid, const char *ssid_sel,
                            const char *pass, char *url, char *token, const char *open,
                            const char *ap_pass, const char *ap_pass2)
{
    app_config_t *nc_p = ncp;
#define nc (*nc_p)
    if (!fit) {
        return send_message(req, "err", T_E_LONG, true);
    }
    trim(token);
    strlcpy(nc.ssid, ssid[0] ? ssid : ssid_sel, sizeof(nc.ssid));
    if (!nc.ssid[0]) {
        return send_message(req, "err", T_E_NOSSID, true);
    }
    if (open[0]) {
        nc.pass[0] = '\0';
    } else if (pass[0]) {
        if (strlen(pass) < 8) {
            return send_message(req, "err", T_E_PASSLEN, true);
        }
        strlcpy(nc.pass, pass, sizeof(nc.pass));
    }
    if (ap_pass[0] || !nc.ap_pass[0]) {
        if (strlen(ap_pass) < 8) {
            return send_message(req, "err", T_E_APPASS, true);
        }
        if (strcmp(ap_pass, ap_pass2) != 0) {
            return send_message(req, "err", T_E_APMATCH, true);
        }
        strlcpy(nc.ap_pass, ap_pass, sizeof(nc.ap_pass));
    }
    if (!config_normalize_url(url)) {
        return send_message(req, "err", T_E_URL, true);
    }
    strlcpy(nc.url, url, sizeof(nc.url));
    if (token[0]) {
        strlcpy(nc.token, token, sizeof(nc.token));
    }
    if (!nc.token[0]) {
        return send_message(req, "err", T_E_TOKEN, true);
    }
    if (config_save(&nc) != ESP_OK) {
        return send_message(req, "err", T_E_FLASH, true);
    }
    s_cfg = nc;
#undef nc
    esp_err_t err = send_message(req, "ok", T_OK_SAVED, false);
    schedule_restart();
    return err;
}

static esp_err_t save_post(httpd_req_t *req)
{
    if (req->content_len <= 0 || req->content_len >= FORM_MAX) {
        return send_message(req, "err", T_E_FORM, true);
    }
    static char body[FORM_MAX];
    int got = 0;
    while (got < (int)req->content_len) {
        int r = httpd_req_recv(req, body + got, req->content_len - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (r <= 0) {
            return ESP_FAIL;
        }
        got += r;
    }
    body[got] = '\0';

    app_config_t nc = s_cfg;
    char ssid[CFG_SSID_MAX + 1], ssid_sel[CFG_SSID_MAX + 1], pass[CFG_PASS_MAX + 1];
    char url[CFG_URL_MAX + 1], token[CFG_TOKEN_MAX + 1], open[4];
    char ap_pass[CFG_PASS_MAX + 1], ap_pass2[CFG_PASS_MAX + 1];
    bool fit = form_get(body, "ssid", ssid, sizeof(ssid)) &&
               form_get(body, "ssid_sel", ssid_sel, sizeof(ssid_sel)) &&
               form_get(body, "pass", pass, sizeof(pass)) &&
               form_get(body, "url", url, sizeof(url)) &&
               form_get(body, "token", token, sizeof(token)) &&
               form_get(body, "open", open, sizeof(open)) &&
               form_get(body, "ap_pass", ap_pass, sizeof(ap_pass)) &&
               form_get(body, "ap_pass2", ap_pass2, sizeof(ap_pass2));
    memset(body, 0, sizeof(body));      /* Passwort/Token nicht im RAM liegen lassen */
    esp_err_t res = save_apply(req, &nc, fit, ssid, ssid_sel, pass, url, token, open, ap_pass, ap_pass2);
    memset(pass, 0, sizeof(pass));
    memset(token, 0, sizeof(token));
    memset(ap_pass, 0, sizeof(ap_pass));
    memset(ap_pass2, 0, sizeof(ap_pass2));
    memset(&nc, 0, sizeof(nc));
    return res;
}

static esp_err_t restart_post(httpd_req_t *req)
{
    esp_err_t err = send_message(req, "ok", T_OK_RESTART, false);
    schedule_restart();
    return err;
}

/* Captive Portal: alles Unbekannte (z. B. /generate_204, /hotspot-detect.html) auf die Startseite umleiten. */
static esp_err_t redirect_404(httpd_req_t *req, httpd_err_code_t code)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://" AP_IP_STR "/");
    return httpd_resp_send(req, NULL, 0);
}

/* Captive-Portal-Erkennung (iOS, Android, Windows): explizit auf die Startseite umleiten. */
static esp_err_t captive_redirect(httpd_req_t *req)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://" AP_IP_STR "/");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    detect_lang(req);
    char page[200];
    snprintf(page, sizeof(page), "<!DOCTYPE html><html lang=\"%s\"><body><a href=\"http://" AP_IP_STR "/\">%s</a></body></html>",
             s_lang ? "en" : "de", T(T_CAPTIVE));
    return httpd_resp_sendstr(req, page);
}

static void start_http(void)
{
    httpd_config_t c = HTTPD_DEFAULT_CONFIG();
    c.max_uri_handlers = 16;
    c.max_open_sockets = 7;
    c.lru_purge_enable = true;
    c.stack_size = 6144;
    httpd_handle_t srv = NULL;
    esp_err_t err = httpd_start(&srv, &c);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP-Server: %s", esp_err_to_name(err));
        led_set_state(LED_ERROR);
        return;
    }
    const httpd_uri_t uris[] = {
        { .uri = "/", .method = HTTP_GET, .handler = root_get },
        { .uri = "/status", .method = HTTP_GET, .handler = status_get },
        { .uri = "/scan", .method = HTTP_GET, .handler = scan_get },
        { .uri = "/save", .method = HTTP_POST, .handler = save_post },
        { .uri = "/restart", .method = HTTP_POST, .handler = restart_post },
        { .uri = "/hotspot-detect.html", .method = HTTP_GET, .handler = captive_redirect },
        { .uri = "/library/test/success.html", .method = HTTP_GET, .handler = captive_redirect },
        { .uri = "/generate_204", .method = HTTP_GET, .handler = captive_redirect },
        { .uri = "/gen_204", .method = HTTP_GET, .handler = captive_redirect },
        { .uri = "/connecttest.txt", .method = HTTP_GET, .handler = captive_redirect },
        { .uri = "/ncsi.txt", .method = HTTP_GET, .handler = captive_redirect },
        { .uri = "/redirect", .method = HTTP_GET, .handler = captive_redirect },
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        httpd_register_uri_handler(srv, &uris[i]);
    }
    httpd_register_err_handler(srv, HTTPD_404_NOT_FOUND, redirect_404);
}

/* ------------------------------------------------------------ Captive-Portal-DNS */

/* Beantwortet jede A-Anfrage mit 192.168.4.1, alles andere ohne Antwort-Eintrag. */
static void dns_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGW(TAG, "DNS: socket fehlgeschlagen");
        vTaskDelete(NULL);
        return;
    }
    struct sockaddr_in addr = { .sin_family = AF_INET, .sin_port = htons(53), .sin_addr.s_addr = htonl(INADDR_ANY) };
    if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        ESP_LOGW(TAG, "DNS: bind fehlgeschlagen");
        close(sock);
        vTaskDelete(NULL);
        return;
    }
    uint8_t buf[512];
    const uint8_t ip[4] = { 192, 168, 4, 1 };
    while (true) {
        struct sockaddr_in from;
        socklen_t flen = sizeof(from);
        int n = recvfrom(sock, buf, sizeof(buf), 0, (struct sockaddr *)&from, &flen);
        if (n < 12) {
            continue;
        }
        if ((buf[2] & 0x80) || buf[4] != 0 || buf[5] != 1) {
            continue;   /* keine Anfrage oder nicht genau eine Frage */
        }
        int i = 12;
        while (i < n && buf[i] != 0) {
            if (buf[i] & 0xC0) {
                i = n;  /* Kompression in der Frage: ungültig */
                break;
            }
            i += buf[i] + 1;
        }
        if (i + 5 > n) {
            continue;
        }
        i++;                                    /* Null-Label */
        uint16_t qtype = (buf[i] << 8) | buf[i + 1];
        i += 4;                                 /* QTYPE + QCLASS */
        int len = i;                            /* Zusatzeinträge (EDNS) verwerfen */
        buf[2] = 0x84 | (buf[2] & 0x79);        /* QR=1, AA=1, Opcode/RD übernehmen */
        buf[3] = 0x00;
        buf[6] = 0; buf[7] = 0;                 /* ANCOUNT */
        buf[8] = 0; buf[9] = 0; buf[10] = 0; buf[11] = 0;
        if (qtype == 1 && len + 16 <= (int)sizeof(buf)) {
            buf[7] = 1;
            const uint8_t ans[16] = { 0xC0, 0x0C, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4, ip[0], ip[1], ip[2], ip[3] };
            memcpy(buf + len, ans, sizeof(ans));
            len += sizeof(ans);
        }
        sendto(sock, buf, len, 0, (struct sockaddr *)&from, flen);
    }
}

/* ------------------------------------------------------------ Start */

void portal_run(void)
{
    led_set_state(LED_SETUP);
    config_load(&s_cfg);

    esp_err_t err = esp_netif_init();
    if (err == ESP_OK) {
        err = esp_event_loop_create_default();
        if (err == ESP_ERR_INVALID_STATE) {
            err = ESP_OK;
        }
    }
    esp_netif_create_default_wifi_ap();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    if (err == ESP_OK) err = esp_wifi_init(&init);
    if (err == ESP_OK) err = esp_wifi_set_storage(WIFI_STORAGE_RAM);
    if (err == ESP_OK) err = esp_wifi_set_mode(WIFI_MODE_APSTA);

    uint8_t mac[6] = { 0 };
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf(s_ap_ssid, sizeof(s_ap_ssid), "Avinox-Sync-%02X%02X", mac[4], mac[5]);

    wifi_config_t ap = { 0 };
    strlcpy((char *)ap.ap.ssid, s_ap_ssid, sizeof(ap.ap.ssid));
    ap.ap.ssid_len = strlen(s_ap_ssid);
    ap.ap.channel = 1;
    ap.ap.max_connection = 4;
    if (strlen(s_cfg.ap_pass) >= 8) {
        strlcpy((char *)ap.ap.password, s_cfg.ap_pass, sizeof(ap.ap.password));
        ap.ap.authmode = WIFI_AUTH_WPA2_PSK;
    } else {
        ap.ap.authmode = WIFI_AUTH_OPEN;        /* noch kein Passwort vergeben */
    }
    if (err == ESP_OK) err = esp_wifi_set_config(WIFI_IF_AP, &ap);
    if (err == ESP_OK) err = esp_wifi_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SoftAP-Start fehlgeschlagen: %s", esp_err_to_name(err));
        led_set_state(LED_ERROR);
        while (true) {
            vTaskDelay(pdMS_TO_TICKS(10000));
        }
    }
    ESP_LOGI(TAG, "Einrichtungsmodus: WLAN '%s' (%s), dann http://" AP_IP_STR "/",
             s_ap_ssid, ap.ap.authmode == WIFI_AUTH_OPEN ? "offen" : "WPA2");

    do_scan();
    start_http();
    if (xTaskCreate(dns_task, "dns", 3072, NULL, 4, NULL) != pdPASS) {
        ESP_LOGW(TAG, "DNS-Task nicht gestartet (kein Captive Portal)");
    }
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(60000));
    }
}
