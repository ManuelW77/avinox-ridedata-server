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
        err = httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Zu wenig Speicher");
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

/* ------------------------------------------------------------ Seiten */

static const char PAGE_HEAD[] =
    "<!DOCTYPE html><html lang=\"de\"><head><meta charset=\"utf-8\">"
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
    "</style></head><body><main><h1>Avinox-Sync</h1>";

static const char PAGE_TAIL[] = "</main></body></html>";

static void status_block(buf_t *b)
{
    buf_append(b, "<section><h2>Status</h2><dl><dt>WLAN</dt><dd>");
    if (s_cfg.ssid[0]) {
        buf_append_esc(b, s_cfg.ssid);
    } else {
        buf_append(b, "<i>nicht eingerichtet</i>");
    }
    buf_append(b, "</dd><dt>WLAN-Passwort</dt><dd>");
    buf_append(b, s_cfg.pass[0] ? "gesetzt" : "keins (offenes WLAN)");
    buf_append(b, "</dd><dt>Server</dt><dd>");
    if (s_cfg.url[0]) {
        buf_append_esc(b, s_cfg.url);
    } else {
        buf_append(b, "<i>fehlt</i>");
    }
    buf_append(b, "</dd><dt>Token</dt><dd>");
    buf_append(b, s_cfg.token[0] ? "gesetzt" : "<i>fehlt</i>");
    buf_append(b, "</dd><dt>Letzter Sync</dt><dd>");
    sync_result_t r;
    if (sync_result_load(&r)) {
        char line[256];
        sync_result_format(&r, line, sizeof(line));
        buf_append_esc(b, line);
    } else {
        buf_append(b, "noch keiner");
    }
    buf_append(b, "</dd></dl></section>");
}

static esp_err_t send_message(httpd_req_t *req, const char *cls, const char *text, bool back_link)
{
    buf_t b;
    buf_init(&b);
    buf_append(&b, PAGE_HEAD);
    buf_append(&b, "<section class=\"");
    buf_append(&b, cls);
    buf_append(&b, "\"><p>");
    buf_append_esc(&b, text);
    buf_append(&b, "</p>");
    if (back_link) {
        buf_append(&b, "<p><a href=\"/\">Zurück zum Formular</a></p>");
    }
    buf_append(&b, "</section>");
    buf_append(&b, PAGE_TAIL);
    return buf_send(req, &b);
}

static esp_err_t root_get(httpd_req_t *req)
{
    buf_t b;
    buf_init(&b);
    buf_append(&b, PAGE_HEAD);
    status_block(&b);

    buf_append(&b,
        "<section><h2>Einrichtung</h2><form method=\"post\" action=\"/save\">"
        "<label for=\"sel\">WLAN aus der Liste</label><select id=\"sel\" name=\"ssid_sel\" "
        "onchange=\"if(this.value)document.getElementById('ssid').value=this.value\">"
        "<option value=\"\">– auswählen –</option>");
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
        snprintf(info, sizeof(info), " (%d dBm%s)", s_aps[i].rssi, s_aps[i].authmode == WIFI_AUTH_OPEN ? ", offen" : "");
        buf_append(&b, "<option value=\"");
        buf_append_esc(&b, ssid);
        buf_append(&b, "\">");
        buf_append_esc(&b, ssid);
        buf_append(&b, info);
        buf_append(&b, "</option>");
    }
    buf_append(&b,
        "</select><small><a href=\"/scan\">Netze neu suchen</a> (die Verbindung zum Handy kann kurz abreißen)</small>"
        "<label for=\"pass\">WLAN-Passwort</label>"
        "<input id=\"pass\" name=\"pass\" type=\"password\" maxlength=\"63\" autocomplete=\"off\" placeholder=\"");
    buf_append(&b, s_cfg.pass[0] ? "gesetzt – leer lassen = unverändert" : "");
    buf_append(&b, "\">"
        "<label class=\"cb\"><input type=\"checkbox\" name=\"open\" value=\"1\">Offenes WLAN (kein Passwort)</label>"
        "<label for=\"ssid\">WLAN-Name (SSID)</label>"
        "<input id=\"ssid\" name=\"ssid\" maxlength=\"32\" autocomplete=\"off\" autocapitalize=\"none\" value=\"");
    buf_append_esc(&b, s_cfg.ssid);
    buf_append(&b, "\"><small>Freitext hat Vorrang vor der Auswahl.</small>"
        "<label for=\"url\">Server-URL</label>"
        "<input id=\"url\" name=\"url\" type=\"url\" maxlength=\"200\" autocapitalize=\"none\" "
        "placeholder=\"http://192.168.1.10:8000\" value=\"");
    buf_append_esc(&b, s_cfg.url);
    buf_append(&b, "\">"
        "<label for=\"token\">Upload-Token</label>"
        "<input id=\"token\" name=\"token\" type=\"password\" maxlength=\"128\" autocomplete=\"off\" placeholder=\"");
    buf_append(&b, s_cfg.token[0] ? "gesetzt – leer lassen = unverändert" : "vom Server, siehe README");
    buf_append(&b, "\">"
        "<h2 style=\"margin-top:1.2rem\">Passwort für dieses Einrichtungs-WLAN</h2>"
        "<label for=\"appass\">Neues Passwort (mindestens 8 Zeichen)</label>"
        "<input id=\"appass\" name=\"ap_pass\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\" placeholder=\"");
    buf_append(&b, s_cfg.ap_pass[0] ? "gesetzt – leer lassen = unverändert" : "Pflicht");
    buf_append(&b, "\">"
        "<label for=\"appass2\">Passwort wiederholen</label>"
        "<input id=\"appass2\" name=\"ap_pass2\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\">"
        "<small>Gilt ab dem nächsten Einrichtungsmodus (BOOT 3 s).</small>"
        "<button type=\"submit\">Speichern und neu starten</button></form>"
        "<form method=\"post\" action=\"/restart\"><button class=\"sec\" type=\"submit\">Ohne Änderung neu starten</button></form>"
        "</section>");
    buf_append(&b, PAGE_TAIL);
    return buf_send(req, &b);
}

static esp_err_t status_get(httpd_req_t *req)
{
    buf_t b;
    buf_init(&b);
    buf_append(&b, PAGE_HEAD);
    status_block(&b);
    buf_append(&b, "<p><a href=\"/\">Zur Einrichtung</a></p>");
    buf_append(&b, PAGE_TAIL);
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
        return send_message(req, "err", "Ein Feld ist zu lang.", true);
    }
    trim(token);
    strlcpy(nc.ssid, ssid[0] ? ssid : ssid_sel, sizeof(nc.ssid));
    if (!nc.ssid[0]) {
        return send_message(req, "err", "Bitte ein WLAN auswählen oder den Namen eintragen.", true);
    }
    if (open[0]) {
        nc.pass[0] = '\0';
    } else if (pass[0]) {
        if (strlen(pass) < 8) {
            return send_message(req, "err", "Das WLAN-Passwort muss mindestens 8 Zeichen haben.", true);
        }
        strlcpy(nc.pass, pass, sizeof(nc.pass));
    }
    if (ap_pass[0] || !nc.ap_pass[0]) {
        if (strlen(ap_pass) < 8) {
            return send_message(req, "err", "Bitte ein Passwort für das Einrichtungs-WLAN vergeben (mindestens 8 Zeichen).", true);
        }
        if (strcmp(ap_pass, ap_pass2) != 0) {
            return send_message(req, "err", "Die beiden Passwörter für das Einrichtungs-WLAN stimmen nicht überein.", true);
        }
        strlcpy(nc.ap_pass, ap_pass, sizeof(nc.ap_pass));
    }
    if (!config_normalize_url(url)) {
        return send_message(req, "err", "Die Server-URL muss mit http:// oder https:// beginnen.", true);
    }
    strlcpy(nc.url, url, sizeof(nc.url));
    if (token[0]) {
        strlcpy(nc.token, token, sizeof(nc.token));
    }
    if (!nc.token[0]) {
        return send_message(req, "err", "Bitte den Upload-Token eintragen.", true);
    }
    if (config_save(&nc) != ESP_OK) {
        return send_message(req, "err", "Speichern im Flash fehlgeschlagen.", true);
    }
    s_cfg = nc;
#undef nc
    esp_err_t err = send_message(req, "ok", "Gespeichert. Das Gerät startet neu und verbindet sich mit dem WLAN. "
                                 "Dieses Einrichtungs-WLAN verschwindet danach.", false);
    schedule_restart();
    return err;
}

static esp_err_t save_post(httpd_req_t *req)
{
    if (req->content_len <= 0 || req->content_len >= FORM_MAX) {
        return send_message(req, "err", "Formular leer oder zu groß.", true);
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
    esp_err_t err = send_message(req, "ok", "Neustart ohne Änderung.", false);
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
    return httpd_resp_sendstr(req, "<!DOCTYPE html><html><body><a href=\"http://" AP_IP_STR "/\">Einrichtung öffnen</a></body></html>");
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
