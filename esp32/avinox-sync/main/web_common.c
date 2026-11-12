#include "web_common.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <strings.h>
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_app_desc.h"

static const char *TAG = "web";

#define FORM_MAX        1536

/* ------------------------------------------------------------ Hilfsfunktionen */


void buf_init(buf_t *b)
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

void buf_append(buf_t *b, const char *s)
{
    size_t n = strlen(s);
    if (!buf_reserve(b, n)) {
        return;
    }
    memcpy(b->p + b->len, s, n + 1);
    b->len += n;
}

void buf_append_esc(buf_t *b, const char *s)
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
esp_err_t buf_send(httpd_req_t *req, buf_t *b)
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
bool web_form_get(const char *body, const char *key, char *out, size_t cap)
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

void web_trim(char *s)
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

void web_schedule_restart(void)
{
    const esp_timer_create_args_t args = { .callback = restart_cb, .name = "restart" };
    esp_timer_handle_t t;
    if (esp_timer_create(&args, &t) == ESP_OK && esp_timer_start_once(t, 2000 * 1000) == ESP_OK) {
        return;
    }
    esp_restart();
}

/* ------------------------------------------------------------ Sprache (DE/EN) */

static const char *const TXT[T_COUNT][2] = {
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
    [T_AP_H] = { "Zugangspasswort (Einrichtungs-WLAN und Weboberfläche)", "Access password (setup Wi-Fi and web interface)" },
    [T_AP_NEW] = { "Neues Passwort (mindestens 8 Zeichen)", "New password (at least 8 characters)" },
    [T_PH_REQUIRED] = { "Pflicht", "required" },
    [T_AP_REPEAT] = { "Passwort wiederholen", "Repeat password" },
    [T_AP_NOTE] = { "Schützt das Einrichtungs-WLAN und die Weboberfläche im Betrieb (Benutzer avinox). Nicht das Passwort deines Heim-WLANs verwenden.", "Protects the setup Wi-Fi and the web interface in normal operation (user avinox). Do not use your home Wi-Fi password." },
    [T_LANG_H] = { "Sprache", "Language" },
    [T_LANG_NOTE] = { "Sprache der Weboberfläche. Wird im Browser gespeichert.", "Language of the web interface. Stored in your browser." },
    [T_LANG_SAVE] = { "Speichern", "Save" },
    [T_RESCUE] = { "Notfallmodus: Einrichtungs-WLAN ohne Passwort. Bitte ein neues Zugangspasswort vergeben.",
                   "Emergency mode: setup Wi-Fi without a password. Please set a new access password." },
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
    /* Betriebs-Weboberfläche */
    [T_DEVICE] = { "Gerät", "Device" },
    [T_FIRMWARE] = { "Firmware", "Firmware" },
    [T_IP] = { "IP-Adresse", "IP address" },
    [T_SIGNAL] = { "WLAN-Signal", "Wi-Fi signal" },
    [T_UPTIME] = { "Laufzeit", "Uptime" },
    [T_HEAP] = { "Freier Speicher", "Free memory" },
    [T_USB] = { "USB-Laufwerk", "USB drive" },
    [T_USB_ABSENT] = { "nicht erkannt", "not detected" },
    [T_USB_READY] = { "erkannt", "detected" },
    [T_USB_FAILED] = { "Fehler bei der Anmeldung (Laufwerk abziehen und neu anstecken)", "registration failed (unplug and replug the drive)" },
    [T_USB_HOSTFAIL] = { "USB-Host läuft nicht", "USB host not running" },
    [T_NOT_CONNECTED] = { "nicht verbunden", "not connected" },
    [T_SETTINGS] = { "Einstellungen", "Settings" },
    [T_ACCESS_H] = { "Zugangspasswort", "Access password" },
    [T_ACCESS_NEW] = { "Neues Passwort (mindestens 8 Zeichen)", "New password (at least 8 characters)" },
    [T_ACCESS_NOTE] = { "Login dieser Seite (Benutzer avinox) und Passwort des Einrichtungs-WLANs.",
                        "Login of this page (user avinox) and password of the setup Wi-Fi." },
    [T_WARN_WIFI] = { "Achtung: Bei falschem WLAN-Namen oder -Passwort ist das Gerät danach nicht mehr erreichbar. "
                      "Dann die BOOT-Taste 3 Sekunden halten (Einrichtungsmodus) und neu einrichten.",
                      "Caution: with a wrong Wi-Fi name or password the device is no longer reachable afterwards. "
                      "In that case hold the BOOT button for 3 seconds (setup mode) and set it up again." },
    [T_SETUP_BTN] = { "Einrichtungsmodus starten", "Start setup mode" },
    [T_SETUP_NOTE] = { "Startet neu und öffnet das Einrichtungs-WLAN (wie BOOT-Taste 3 s).",
                       "Restarts and opens the setup Wi-Fi (like holding BOOT for 3 s)." },
    [T_OK_SAVED_WEB] = { "Gespeichert. Das Gerät startet neu und verbindet sich erneut mit dem WLAN. Diese Seite ist in ca. 30 Sekunden wieder erreichbar, sofern das WLAN stimmt.",
                         "Saved. The device restarts and reconnects to the Wi-Fi. This page is available again in about 30 seconds if the Wi-Fi settings are correct." },
    [T_OK_SETUP] = { "Das Gerät startet neu im Einrichtungsmodus (eigenes WLAN Avinox-Sync-XXXX).",
                     "The device restarts in setup mode (its own Wi-Fi Avinox-Sync-XXXX)." },
};

static int s_lang;      /* 0 = de, 1 = en; je Anfrage gesetzt (httpd arbeitet Anfragen nacheinander ab) */

const char *web_t(web_text_t id)
{
    return TXT[id][s_lang];
}

int web_lang(void)
{
    return s_lang;
}

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
void web_detect_lang(httpd_req_t *req)
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
void web_page_tail(buf_t *b)
{
    buf_append(b, PAGE_TAIL);
    buf_append(b, " · Firmware v");
    buf_append_esc(b, esp_app_get_description()->version);
    buf_append(b, PAGE_TAIL_END);
}

/* Seitenkopf samt Sprachumschalter; path = aktuelle Seite für die ?lang=-Links. */
void web_page_head(buf_t *b, const char *path)
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

esp_err_t web_send_message(httpd_req_t *req, const char *cls, web_text_t text, bool back_link)
{
    web_detect_lang(req);
    buf_t b;
    buf_init(&b);
    web_page_head(&b, "/");
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
    web_page_tail(&b);
    return buf_send(req, &b);
}

/* ------------------------------------------------------------ Einstellungsformular */

static bool s_rescue;

void web_set_rescue(bool on)
{
    s_rescue = on;
}

void web_render_rescue_banner(buf_t *b)
{
    if (s_rescue) {
        buf_append(b, "<section class=\"err\"><p><b>");
        buf_append_esc(b, T(T_RESCUE));
        buf_append(b, "</b></p></section>");
    }
}

void web_render_form(buf_t *b, const app_config_t *cfg, const wifi_ap_record_t *aps, uint16_t ap_count, bool portal)
{
    buf_append(b, "<section><h2>");
    buf_append(b, T(T_LANG_H));
    buf_append(b, "</h2><p class=\"hint\">");
    buf_append(b, T(T_LANG_NOTE));
    buf_append(b, "</p><form method=\"get\" action=\"/\"><label for=\"lang-select\">");
    buf_append(b, T(T_LANG_H));
    buf_append(b, "</label><select name=\"lang\" id=\"lang-select\"><option value=\"de\"");
    buf_append(b, s_lang ? "" : " selected");
    buf_append(b, ">Deutsch</option><option value=\"en\"");
    buf_append(b, s_lang ? " selected" : "");
    buf_append(b, ">English</option></select><button type=\"submit\">");
    buf_append(b, T(T_LANG_SAVE));
    buf_append(b, "</button></form></section>");
    buf_append(b, "<section><h2>");
    buf_append(b, portal ? T(T_SETUP) : T(T_SETTINGS));
    buf_append(b, "</h2><form method=\"post\" action=\"/save\"><label for=\"sel\">");
    buf_append(b, T(T_WIFI_LIST));
    buf_append(b,
        "</label><select id=\"sel\" name=\"ssid_sel\" "
        "onchange=\"if(this.value)document.getElementById('ssid').value=this.value\">"
        "<option value=\"\">");
    buf_append(b, T(T_PICK));
    buf_append(b, "</option>");
    for (int i = 0; i < ap_count; i++) {
        const char *ssid = (const char *)aps[i].ssid;
        if (!ssid[0]) {
            continue;
        }
        bool dup = false;
        for (int j = 0; j < i; j++) {
            if (strcmp(ssid, (const char *)aps[j].ssid) == 0) {
                dup = true;
                break;
            }
        }
        if (dup) {
            continue;
        }
        char info[48];
        snprintf(info, sizeof(info), " (%d dBm%s)", aps[i].rssi, aps[i].authmode == WIFI_AUTH_OPEN ? T(T_OPEN_SUFFIX) : "");
        buf_append(b, "<option value=\"");
        buf_append_esc(b, ssid);
        buf_append(b, "\">");
        buf_append_esc(b, ssid);
        buf_append(b, info);
        buf_append(b, "</option>");
    }
    buf_append(b, "</select><small><a href=\"/scan\">");
    buf_append(b, T(T_RESCAN));
    buf_append(b, "</a>");
    if (portal) {
        buf_append(b, T(T_RESCAN_NOTE));
    }
    buf_append(b, "</small><label for=\"pass\">");
    buf_append(b, T(T_PASS_LBL));
    buf_append(b, "</label>"
        "<input id=\"pass\" name=\"pass\" type=\"password\" maxlength=\"63\" autocomplete=\"off\" placeholder=\"");
    buf_append(b, cfg->pass[0] ? T(T_PH_SET_KEEP) : "");
    buf_append(b, "\"><label class=\"cb\"><input type=\"checkbox\" name=\"open\" value=\"1\">");
    buf_append(b, T(T_OPEN_NET));
    buf_append(b, "</label><label for=\"ssid\">");
    buf_append(b, T(T_SSID_LBL));
    buf_append(b, "</label>"
        "<input id=\"ssid\" name=\"ssid\" maxlength=\"32\" autocomplete=\"off\" autocapitalize=\"none\" value=\"");
    buf_append_esc(b, cfg->ssid);
    buf_append(b, "\"><small>");
    buf_append(b, T(T_SSID_NOTE));
    buf_append(b, "</small><label for=\"url\">");
    buf_append(b, T(T_URL_LBL));
    buf_append(b, "</label>"
        "<input id=\"url\" name=\"url\" type=\"url\" maxlength=\"200\" autocapitalize=\"none\" "
        "placeholder=\"http://192.168.1.10:8000\" value=\"");
    buf_append_esc(b, cfg->url);
    buf_append(b, "\"><label for=\"token\">");
    buf_append(b, T(T_TOKEN_LBL));
    buf_append(b, "</label>"
        "<input id=\"token\" name=\"token\" type=\"password\" maxlength=\"128\" autocomplete=\"off\" placeholder=\"");
    buf_append(b, cfg->token[0] ? T(T_PH_SET_KEEP) : T(T_PH_TOKEN));
    buf_append(b, "\"><h2 style=\"margin-top:1.2rem\">");
    buf_append(b, portal ? T(T_AP_H) : T(T_ACCESS_H));
    buf_append(b, "</h2><label for=\"appass\">");
    buf_append(b, portal ? T(T_AP_NEW) : T(T_ACCESS_NEW));
    buf_append(b, "</label>"
        "<input id=\"appass\" name=\"ap_pass\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\" placeholder=\"");
    buf_append(b, cfg->ap_pass[0] ? T(T_PH_SET_KEEP) : T(T_PH_REQUIRED));
    buf_append(b, "\"><label for=\"appass2\">");
    buf_append(b, T(T_AP_REPEAT));
    buf_append(b, "</label>"
        "<input id=\"appass2\" name=\"ap_pass2\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\">"
        "<small>");
    buf_append(b, portal ? T(T_AP_NOTE) : T(T_ACCESS_NOTE));
    buf_append(b, "</small><button type=\"submit\">");
    buf_append(b, T(T_SAVE_BTN));
    buf_append(b, "</button></form><form method=\"post\" action=\"/restart\"><button class=\"sec\" type=\"submit\">");
    buf_append(b, T(T_RESTART_BTN));
    buf_append(b, "</button></form></section>");
}

int web_scan(wifi_ap_record_t *aps, uint16_t max)
{
    wifi_scan_config_t sc = { .show_hidden = false };
    esp_err_t err = esp_wifi_scan_start(&sc, true);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "WLAN-Suche: %s", esp_err_to_name(err));
        return -1;
    }
    uint16_t n = max;
    if (esp_wifi_scan_get_ap_records(&n, aps) != ESP_OK) {
        return -1;
    }
    ESP_LOGI(TAG, "WLAN-Suche: %u Netze", n);
    return n;
}

/* ------------------------------------------------------------ Speichern */

/* Prüft die Formularwerte, speichert und antwortet. Aufrufer nullt die Passwortpuffer. */
static esp_err_t save_apply(httpd_req_t *req, app_config_t *cur, app_config_t *nc, bool portal, bool fit,
                            const char *ssid, const char *ssid_sel, const char *pass, char *url, char *token,
                            const char *open, const char *ap_pass, const char *ap_pass2)
{
    if (!fit) {
        return web_send_message(req, "err", T_E_LONG, true);
    }
    web_trim(token);
    strlcpy(nc->ssid, ssid[0] ? ssid : ssid_sel, sizeof(nc->ssid));
    if (!nc->ssid[0]) {
        return web_send_message(req, "err", T_E_NOSSID, true);
    }
    if (open[0]) {
        nc->pass[0] = '\0';
    } else if (pass[0]) {
        if (strlen(pass) < 8) {
            return web_send_message(req, "err", T_E_PASSLEN, true);
        }
        strlcpy(nc->pass, pass, sizeof(nc->pass));
    }
    if (s_rescue || ap_pass[0] || !nc->ap_pass[0]) {      /* Notfallmodus: altes Passwort nie behalten */
        if (strlen(ap_pass) < 8) {
            return web_send_message(req, "err", T_E_APPASS, true);
        }
        if (strcmp(ap_pass, ap_pass2) != 0) {
            return web_send_message(req, "err", T_E_APMATCH, true);
        }
        strlcpy(nc->ap_pass, ap_pass, sizeof(nc->ap_pass));
    }
    if (!config_normalize_url(url)) {
        return web_send_message(req, "err", T_E_URL, true);
    }
    strlcpy(nc->url, url, sizeof(nc->url));
    if (token[0]) {
        strlcpy(nc->token, token, sizeof(nc->token));
    }
    if (!nc->token[0]) {
        return web_send_message(req, "err", T_E_TOKEN, true);
    }
    if (config_save(nc) != ESP_OK) {
        return web_send_message(req, "err", T_E_FLASH, true);
    }
    *cur = *nc;
    esp_err_t err = web_send_message(req, "ok", portal ? T_OK_SAVED : T_OK_SAVED_WEB, false);
    web_schedule_restart();
    return err;
}

esp_err_t web_handle_save(httpd_req_t *req, app_config_t *cur, bool portal)
{
    if (req->content_len <= 0 || req->content_len >= FORM_MAX) {
        return web_send_message(req, "err", T_E_FORM, true);
    }
    static char body[FORM_MAX];
    int got = 0;
    while (got < (int)req->content_len) {
        int r = httpd_req_recv(req, body + got, req->content_len - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (r <= 0) {
            memset(body, 0, sizeof(body));
            return ESP_FAIL;
        }
        got += r;
    }
    body[got] = '\0';

    app_config_t nc = *cur;
    char ssid[CFG_SSID_MAX + 1], ssid_sel[CFG_SSID_MAX + 1], pass[CFG_PASS_MAX + 1];
    char url[CFG_URL_MAX + 1], token[CFG_TOKEN_MAX + 1], open[4];
    char ap_pass[CFG_PASS_MAX + 1], ap_pass2[CFG_PASS_MAX + 1];
    bool fit = web_form_get(body, "ssid", ssid, sizeof(ssid)) &&
               web_form_get(body, "ssid_sel", ssid_sel, sizeof(ssid_sel)) &&
               web_form_get(body, "pass", pass, sizeof(pass)) &&
               web_form_get(body, "url", url, sizeof(url)) &&
               web_form_get(body, "token", token, sizeof(token)) &&
               web_form_get(body, "open", open, sizeof(open)) &&
               web_form_get(body, "ap_pass", ap_pass, sizeof(ap_pass)) &&
               web_form_get(body, "ap_pass2", ap_pass2, sizeof(ap_pass2));
    memset(body, 0, sizeof(body));      /* Passwort/Token nicht im RAM liegen lassen */
    esp_err_t res = save_apply(req, cur, &nc, portal, fit, ssid, ssid_sel, pass, url, token, open, ap_pass, ap_pass2);
    memset(pass, 0, sizeof(pass));
    memset(token, 0, sizeof(token));
    memset(ap_pass, 0, sizeof(ap_pass));
    memset(ap_pass2, 0, sizeof(ap_pass2));
    memset(&nc, 0, sizeof(nc));
    return res;
}
