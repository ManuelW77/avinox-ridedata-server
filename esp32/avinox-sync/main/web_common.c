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
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "cJSON.h"

#include "report.h"

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
    [T_RESCUE] = { "Notfallmodus: Einrichtungs-WLAN ohne Passwort. Bitte ein neues Zugangspasswort vergeben und den Upload-Token neu eingeben (steht auf dem Server unter Einstellungen).",
                   "Emergency mode: setup Wi-Fi without a password. Please set a new access password and enter the upload token again (shown on the server under Settings)." },
    [T_E_RESCUE_TOKEN] = { "Notfallmodus: Bitte den Upload-Token neu eingeben.",
                           "Emergency mode: please enter the upload token again." },
    [T_E_RESCUE_WIFI] = { "Notfallmodus: Bei geändertem WLAN-Namen muss das WLAN-Passwort neu eingegeben werden.",
                          "Emergency mode: if the Wi-Fi name is changed, the Wi-Fi password must be entered again." },
    [T_CHECK_BTN] = { "Verbindung prüfen", "Check connection" },
    [T_CHECK_NOTE] = { "Während der Prüfung kann die Verbindung zu diesem WLAN kurz abreißen.",
                       "During the check the connection to this Wi-Fi may drop briefly." },
    [T_CK_RUNNING] = { "Prüfe …", "Checking …" },
    [T_CK_LOST] = { "Verbindung zum Gerät unterbrochen – bitte neu verbinden und erneut prüfen.",
                    "Connection to the device lost – please reconnect and check again." },
    [T_CK_BUSY] = { "Prüfung läuft bereits.", "A check is already running." },
    [T_CK_OK] = { "Server und Token in Ordnung.", "Server and token are OK." },
    [T_CK_SERVER] = { "Server nicht erreichbar.", "Server not reachable." },
    [T_CK_TOKEN] = { "Token falsch.", "Token wrong." },
    [T_CK_HTTP] = { "Server antwortet mit HTTP %d.", "Server answered with HTTP %d." },
    [T_CK_WIFI] = { "WLAN-Verbindung fehlgeschlagen (Grund %d).", "Wi-Fi connection failed (reason %d)." },
    [T_CK_WIFI_NOTFOUND] = { "WLAN-Verbindung fehlgeschlagen: Netz nicht gefunden.", "Wi-Fi connection failed: network not found." },
    [T_CK_WIFI_AUTH] = { "WLAN-Verbindung fehlgeschlagen: Passwort falsch.", "Wi-Fi connection failed: wrong password." },
    [T_CK_WIFI_TIMEOUT] = { "WLAN-Verbindung fehlgeschlagen: keine Antwort (Zeitüberschreitung).",
                            "Wi-Fi connection failed: no response (timeout)." },
    [T_CK_RESCUE_PASS] = { "Notfallmodus: Zum Prüfen bitte das WLAN-Passwort eingeben.",
                           "Emergency mode: please enter the Wi-Fi password to check." },
    [T_CK_MEM] = { "Zu wenig Speicher für die Prüfung.", "Not enough memory for the check." },
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
    "#ckres{margin-top:.6rem;padding:.5rem .7rem;border-radius:6px}#ckres:empty{display:none}"
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

/* Knopf "Verbindung prüfen": ohne JavaScript bleibt er verborgen, das Formular geht normal weiter. */
static const char CHECK_JS[] =
    "<script>(function(){var b=document.getElementById('ckbtn'),r=document.getElementById('ckres');"
    "if(!b||!r||!window.fetch||!window.URLSearchParams||!window.AbortController)return;"
    "var f=b.form,n=['url','token','ssid','ssid_sel','pass','open'];b.hidden=false;"
    "b.onclick=function(){var d=new URLSearchParams(),i,e;"
    "for(i=0;i<n.length;i++){e=f.elements[n[i]];if(e&&!(e.type=='checkbox'&&!e.checked))d.append(n[i],e.value)}"
    "r.className='hint';r.textContent=r.getAttribute('data-run');b.disabled=true;"
    "var c=new AbortController(),t=setTimeout(function(){c.abort()},+r.getAttribute('data-to'));"
    "fetch('/check',{method:'POST',body:d,signal:c.signal,cache:'no-store'})"
    ".then(function(x){return x.json()})"
    ".then(function(j){r.className=j.ok?'ok':'err';r.textContent=j.msg})"
    ".catch(function(){r.className='err';r.textContent=r.getAttribute('data-lost')})"
    ".then(function(){clearTimeout(t);b.disabled=false})}})();</script>";

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
    buf_append(b, "\"><button type=\"button\" class=\"sec\" id=\"ckbtn\" hidden>");
    buf_append(b, T(T_CHECK_BTN));
    buf_append(b, "</button><div id=\"ckres\" role=\"status\" aria-live=\"polite\" data-run=\"");
    buf_append_esc(b, T(T_CK_RUNNING));
    buf_append(b, "\" data-lost=\"");
    buf_append_esc(b, T(T_CK_LOST));
    buf_append(b, portal ? "\" data-to=\"30000\"></div>" : "\" data-to=\"20000\"></div>");
    if (portal) {
        buf_append(b, "<small>");
        buf_append_esc(b, T(T_CHECK_NOTE));
        buf_append(b, "</small>");
    }
    buf_append(b, CHECK_JS);
    buf_append(b, "<h2 style=\"margin-top:1.2rem\">");
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
    /* Notfallmodus: Zugangsdaten dürfen nicht stillschweigend an ein anderes Ziel umgeleitet werden. */
    if (s_rescue && strcmp(nc->ssid, cur->ssid) != 0 && !open[0] && !pass[0]) {
        return web_send_message(req, "err", T_E_RESCUE_WIFI, true);
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
    if (s_rescue && !token[0]) {        /* gespeicherten Token nie übernehmen: sonst über anderes WLAN/URL abgreifbar */
        return web_send_message(req, "err", T_E_RESCUE_TOKEN, true);
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

/* Formularkörper in s_body lesen (httpd arbeitet Anfragen nacheinander ab). Aufrufer nullt s_body nach Gebrauch. */
static char s_body[FORM_MAX];

static bool form_recv(httpd_req_t *req)
{
    int got = 0;
    while (got < (int)req->content_len) {
        int r = httpd_req_recv(req, s_body + got, req->content_len - got);
        if (r == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (r <= 0) {
            memset(s_body, 0, sizeof(s_body));
            return false;
        }
        got += r;
    }
    s_body[got] = '\0';
    return true;
}

esp_err_t web_handle_save(httpd_req_t *req, app_config_t *cur, bool portal)
{
    if (req->content_len <= 0 || req->content_len >= FORM_MAX) {
        return web_send_message(req, "err", T_E_FORM, true);
    }
    char *body = s_body;
    if (!form_recv(req)) {
        return ESP_FAIL;
    }

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
    memset(body, 0, FORM_MAX);          /* Passwort/Token nicht im RAM liegen lassen */
    esp_err_t res = save_apply(req, cur, &nc, portal, fit, ssid, ssid_sel, pass, url, token, open, ap_pass, ap_pass2);
    memset(pass, 0, sizeof(pass));
    memset(token, 0, sizeof(token));
    memset(ap_pass, 0, sizeof(ap_pass));
    memset(ap_pass2, 0, sizeof(ap_pass2));
    memset(&nc, 0, sizeof(nc));
    return res;
}

/* ------------------------------------------------------------ Verbindung prüfen */

#define CHECK_HTTP_TIMEOUT_MS   8000
#define CHECK_TASK_STACK        10240   /* TLS-Handshake bei https-URL */

typedef struct {
    char url[CFG_URL_MAX + 1];
    char token[CFG_TOKEN_MAX + 1];
    char ssid[CFG_SSID_MAX + 1];
    char pass[CFG_PASS_MAX + 1];
    const web_wifi_probe_t *wifi;
    bool ok;
    const char *step;       /* "wifi" | "server" | "token" | "ok" */
    web_text_t msg;
    int arg;                /* Zahl für Texte mit %d */
} check_job_t;

static check_job_t s_job;
static SemaphoreHandle_t s_check_done;
static volatile int s_check_busy;

static esp_err_t check_reply(httpd_req_t *req, bool ok, const char *step, web_text_t msg, int arg)
{
    char text[160];
    snprintf(text, sizeof(text), T(msg), arg);      /* Texte ohne %d ignorieren arg */
    cJSON *o = cJSON_CreateObject();
    char *json = NULL;
    if (o) {
        cJSON_AddBoolToObject(o, "ok", ok);
        cJSON_AddStringToObject(o, "step", step);
        cJSON_AddStringToObject(o, "msg", text);
        json = cJSON_PrintUnformatted(o);
        cJSON_Delete(o);
    }
    if (!json) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    }
    httpd_resp_set_type(req, "application/json; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t err = httpd_resp_sendstr(req, json);
    cJSON_free(json);
    return err;
}

static void check_task(void *arg)
{
    check_job_t *j = &s_job;
    int st;
    j->ok = false;
    j->step = "server";
    j->msg = T_CK_SERVER;
    j->arg = 0;
    if (j->wifi) {
        int reason = 0;
        web_wifi_result_t r = j->wifi->connect(j->ssid, j->pass, &reason);
        memset(j->pass, 0, sizeof(j->pass));
        if (r != WIFI_PROBE_OK) {
            j->step = "wifi";
            j->msg = r == WIFI_PROBE_NOT_FOUND ? T_CK_WIFI_NOTFOUND
                   : r == WIFI_PROBE_AUTH ? T_CK_WIFI_AUTH
                   : r == WIFI_PROBE_TIMEOUT ? T_CK_WIFI_TIMEOUT : T_CK_WIFI;
            j->arg = reason;
            goto out;
        }
    }
    st = report_probe_manifest(j->url, j->token, CHECK_HTTP_TIMEOUT_MS);
    ESP_LOGI(TAG, "Verbindung prüfen: HTTP %d", st);
    if (st == 200) {
        j->ok = true;
        j->step = "ok";
        j->msg = T_CK_OK;
    } else if (st == 401 || st == 403) {
        j->step = "token";
        j->msg = T_CK_TOKEN;
    } else if (st > 0) {
        j->msg = T_CK_HTTP;
        j->arg = st;
    }
out:
    if (j->wifi) {
        j->wifi->release();
    }
    memset(j->token, 0, sizeof(j->token));
    memset(j->pass, 0, sizeof(j->pass));
    xSemaphoreGive(s_check_done);
    vTaskDelete(NULL);
}

static esp_err_t check_apply(httpd_req_t *req, const app_config_t *cur, const web_wifi_probe_t *wifi, bool fit,
                             const char *ssid, const char *ssid_sel, const char *pass, char *url, char *token,
                             const char *open)
{
    if (!fit) {
        return check_reply(req, false, "server", T_E_LONG, 0);
    }
    web_trim(token);
    if (!config_normalize_url(url)) {
        return check_reply(req, false, "server", T_E_URL, 0);
    }
    if (!token[0]) {
        /* Notfallmodus: gespeicherten Token nie an eine (evtl. fremde) URL schicken. */
        if (s_rescue) {
            return check_reply(req, false, "token", T_E_RESCUE_TOKEN, 0);
        }
        if (!cur->token[0]) {
            return check_reply(req, false, "token", T_E_TOKEN, 0);
        }
    }
    const char *use_ssid = ssid[0] ? ssid : ssid_sel;
    const char *use_pass = "";
    if (wifi) {
        if (!use_ssid[0]) {
            return check_reply(req, false, "wifi", T_E_NOSSID, 0);
        }
        if (open[0]) {
            use_pass = "";
        } else if (pass[0]) {
            if (strlen(pass) < 8) {
                return check_reply(req, false, "wifi", T_E_PASSLEN, 0);
            }
            use_pass = pass;
        } else if (s_rescue) {
            return check_reply(req, false, "wifi", T_CK_RESCUE_PASS, 0);
        } else if (strcmp(use_ssid, cur->ssid) == 0) {
            use_pass = cur->pass;
        }
    }
    if (!s_check_done) {
        s_check_done = xSemaphoreCreateBinary();
        if (!s_check_done) {
            return check_reply(req, false, "server", T_CK_MEM, 0);
        }
    }
    if (__sync_lock_test_and_set(&s_check_busy, 1)) {
        return check_reply(req, false, "server", T_CK_BUSY, 0);
    }
    memset(&s_job, 0, sizeof(s_job));
    strlcpy(s_job.url, url, sizeof(s_job.url));
    strlcpy(s_job.token, token[0] ? token : cur->token, sizeof(s_job.token));
    strlcpy(s_job.ssid, use_ssid, sizeof(s_job.ssid));
    strlcpy(s_job.pass, use_pass, sizeof(s_job.pass));
    s_job.wifi = wifi;
    if (xTaskCreate(check_task, "check", CHECK_TASK_STACK, NULL, 4, NULL) != pdPASS) {
        memset(&s_job, 0, sizeof(s_job));
        __sync_lock_release(&s_check_busy);
        return check_reply(req, false, "server", T_CK_MEM, 0);
    }
    xSemaphoreTake(s_check_done, portMAX_DELAY);    /* Task endet nach Server-Timeout bzw. spätestens ~25 s */
    esp_err_t err = check_reply(req, s_job.ok, s_job.step, s_job.msg, s_job.arg);
    memset(&s_job, 0, sizeof(s_job));
    __sync_lock_release(&s_check_busy);
    return err;
}

esp_err_t web_handle_check(httpd_req_t *req, const app_config_t *cur, const web_wifi_probe_t *wifi)
{
    web_detect_lang(req);
    if (req->content_len <= 0 || req->content_len >= FORM_MAX) {
        return check_reply(req, false, "server", T_E_FORM, 0);
    }
    if (!form_recv(req)) {
        return ESP_FAIL;
    }
    char ssid[CFG_SSID_MAX + 1], ssid_sel[CFG_SSID_MAX + 1], pass[CFG_PASS_MAX + 1];
    char url[CFG_URL_MAX + 1], token[CFG_TOKEN_MAX + 1], open[4];
    bool fit = web_form_get(s_body, "ssid", ssid, sizeof(ssid)) &&
               web_form_get(s_body, "ssid_sel", ssid_sel, sizeof(ssid_sel)) &&
               web_form_get(s_body, "pass", pass, sizeof(pass)) &&
               web_form_get(s_body, "url", url, sizeof(url)) &&
               web_form_get(s_body, "token", token, sizeof(token)) &&
               web_form_get(s_body, "open", open, sizeof(open));
    memset(s_body, 0, sizeof(s_body));
    esp_err_t res = check_apply(req, cur, wifi, fit, ssid, ssid_sel, pass, url, token, open);
    memset(pass, 0, sizeof(pass));
    memset(token, 0, sizeof(token));
    return res;
}
