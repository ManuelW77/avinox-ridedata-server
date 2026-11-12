#include "webui.h"

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_http_server.h"

#include "report.h"
#include "sync.h"
#include "usb_drive.h"
#include "web_auth.h"
#include "web_common.h"
#include "wifi_sta.h"

static const char *TAG = "webui";

#define WEB_USER        "admin"
#define MAX_APS         20
#define MAX_FAILS       5
#define LOCK_US         (30LL * 1000000)

static app_config_t s_cfg;
static wifi_ap_record_t s_aps[MAX_APS];
static uint16_t s_ap_count;
static int s_fails;
static int64_t s_lock_until_us;

/* ------------------------------------------------------------ Zugriffsschutz */

static esp_err_t reply_plain(httpd_req_t *req, const char *status, const char *text)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "text/plain; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_sendstr(req, text);
}

/* true = Anfrage darf weiter. Sonst ist die Antwort (429/401/403) schon gesendet. */
static bool access_ok(httpd_req_t *req, bool post)
{
    int64_t now = esp_timer_get_time();
    if (now < s_lock_until_us) {
        char retry[12];
        snprintf(retry, sizeof(retry), "%d", (int)((s_lock_until_us - now) / 1000000 + 1));
        httpd_resp_set_hdr(req, "Retry-After", retry);
        reply_plain(req, "429 Too Many Requests", "Zu viele Fehlversuche, bitte warten. / Too many failed attempts, please wait.");
        return false;
    }

    char auth[192];
    esp_err_t e = httpd_req_get_hdr_value_str(req, "Authorization", auth, sizeof(auth));
    bool have = e == ESP_OK || e == ESP_ERR_HTTPD_RESULT_TRUNC;
    bool valid = e == ESP_OK && web_basic_auth_valid(auth, WEB_USER, s_cfg.ap_pass);
    memset(auth, 0, sizeof(auth));
    if (!valid) {
        if (have && ++s_fails >= MAX_FAILS) {
            s_fails = 0;
            s_lock_until_us = now + LOCK_US;
            ESP_LOGW(TAG, "%d Fehlversuche: Anmeldung 30 s gesperrt", MAX_FAILS);
        }
        httpd_resp_set_hdr(req, "WWW-Authenticate", "Basic realm=\"Avinox-Sync\", charset=\"UTF-8\"");
        reply_plain(req, "401 Unauthorized", "Anmeldung erforderlich. / Authentication required.");
        return false;
    }
    s_fails = 0;

    if (post) {
        char origin[96], host[96];
        if (httpd_req_get_hdr_value_str(req, "Origin", origin, sizeof(origin)) != ESP_ERR_NOT_FOUND) {
            bool match = httpd_req_get_hdr_value_str(req, "Host", host, sizeof(host)) == ESP_OK &&
                         web_origin_matches_host(origin, host);
            if (!match) {
                reply_plain(req, "403 Forbidden", "Anfrage von fremder Seite abgelehnt. / Cross-site request rejected.");
                return false;
            }
        }
    }
    return true;
}

/* ------------------------------------------------------------ Seiten */

static void row(buf_t *b, web_text_t label, const char *value_escaped)
{
    buf_append(b, "<dt>");
    buf_append(b, T(label));
    buf_append(b, "</dt><dd>");
    buf_append_esc(b, value_escaped);
    buf_append(b, "</dd>");
}

static void status_block(buf_t *b)
{
    char tmp[64];
    buf_append(b, "<section><h2>");
    buf_append(b, T(T_STATUS));
    buf_append(b, "</h2><dl>");

    row(b, T_FIRMWARE, esp_app_get_description()->version);
    row(b, T_DEVICE, report_device_id());

    wifi_ap_record_t ap;
    bool linked = wifi_sta_connected() && esp_wifi_sta_get_ap_info(&ap) == ESP_OK;
    row(b, T_WIFI, linked ? (const char *)ap.ssid : (s_cfg.ssid[0] ? s_cfg.ssid : T(T_NOT_SET)));
    if (linked) {
        snprintf(tmp, sizeof(tmp), "%d dBm", ap.rssi);
        row(b, T_SIGNAL, tmp);
    } else {
        row(b, T_SIGNAL, T(T_NOT_CONNECTED));
    }

    esp_netif_ip_info_t ip;
    esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (sta && esp_netif_get_ip_info(sta, &ip) == ESP_OK && ip.ip.addr != 0) {
        snprintf(tmp, sizeof(tmp), IPSTR, IP2STR(&ip.ip));
    } else {
        strlcpy(tmp, "-", sizeof(tmp));
    }
    row(b, T_IP, tmp);

    unsigned s = (unsigned)(esp_timer_get_time() / 1000000);
    snprintf(tmp, sizeof(tmp), "%ud %02uh %02um", s / 86400, (s / 3600) % 24, (s / 60) % 60);
    row(b, T_UPTIME, tmp);
    snprintf(tmp, sizeof(tmp), "%u kB", (unsigned)(esp_get_free_heap_size() / 1024));
    row(b, T_HEAP, tmp);

    drive_state_t ds = usb_drive_state();
    row(b, T_USB, usb_drive_host_failed() ? T(T_USB_HOSTFAIL)
                  : ds == DRIVE_READY ? T(T_USB_READY)
                  : ds == DRIVE_FAILED ? T(T_USB_FAILED) : T(T_USB_ABSENT));

    sync_result_t r;
    if (sync_result_load(&r)) {
        char line[256];
        sync_result_format(&r, line, sizeof(line));
        row(b, T_LAST_SYNC, line);
    } else {
        row(b, T_LAST_SYNC, T(T_NONE_YET));
    }
    row(b, T_SERVER, s_cfg.url[0] ? s_cfg.url : T(T_MISSING));
    buf_append(b, "</dl></section>");
}

static esp_err_t root_get(httpd_req_t *req)
{
    if (!access_ok(req, false)) {
        return ESP_OK;
    }
    web_detect_lang(req);
    buf_t b;
    buf_init(&b);
    web_page_head(&b, "/");
    status_block(&b);
    buf_append(&b, "<section class=\"err\"><p>");
    buf_append_esc(&b, T(T_WARN_WIFI));
    buf_append(&b, "</p></section>");
    web_render_form(&b, &s_cfg, s_aps, s_ap_count, false);
    buf_append(&b, "<section><form method=\"post\" action=\"/setup\"><button class=\"sec\" type=\"submit\">");
    buf_append(&b, T(T_SETUP_BTN));
    buf_append(&b, "</button></form><small>");
    buf_append_esc(&b, T(T_SETUP_NOTE));
    buf_append(&b, "</small></section>");
    web_page_tail(&b);
    return buf_send(req, &b);
}

static esp_err_t scan_get(httpd_req_t *req)
{
    if (!access_ok(req, false)) {
        return ESP_OK;
    }
    int n = web_scan(s_aps, MAX_APS);
    if (n >= 0) {
        s_ap_count = n;
    }
    httpd_resp_set_status(req, "303 See Other");
    httpd_resp_set_hdr(req, "Location", "/");
    return httpd_resp_send(req, NULL, 0);
}

static esp_err_t save_post(httpd_req_t *req)
{
    if (!access_ok(req, true)) {
        return ESP_OK;
    }
    return web_handle_save(req, &s_cfg, false);
}

static esp_err_t restart_post(httpd_req_t *req)
{
    if (!access_ok(req, true)) {
        return ESP_OK;
    }
    esp_err_t err = web_send_message(req, "ok", T_OK_RESTART, false);
    web_schedule_restart();
    return err;
}

static esp_err_t setup_post(httpd_req_t *req)
{
    if (!access_ok(req, true)) {
        return ESP_OK;
    }
    if (config_request_portal() != ESP_OK) {
        return web_send_message(req, "err", T_E_FLASH, true);
    }
    esp_err_t err = web_send_message(req, "ok", T_OK_SETUP, false);
    web_schedule_restart();
    return err;
}

/* Auch unbekannte Adressen und falsche Methoden erst nach der Anmeldung beantworten. */
static esp_err_t error_handler(httpd_req_t *req, httpd_err_code_t code)
{
    if (!access_ok(req, req->method == HTTP_POST)) {
        return ESP_OK;
    }
    return code == HTTPD_405_METHOD_NOT_ALLOWED
               ? reply_plain(req, "405 Method Not Allowed", "405")
               : reply_plain(req, "404 Not Found", "404");
}

/* ------------------------------------------------------------ Start */

void webui_start(const app_config_t *cfg)
{
    if (strlen(cfg->ap_pass) < 8) {
        ESP_LOGW(TAG, "Kein Zugangspasswort (Einrichtungs-Passwort) gesetzt: Weboberfläche bleibt aus");
        return;
    }
    s_cfg = *cfg;

    httpd_config_t c = HTTPD_DEFAULT_CONFIG();
    c.max_uri_handlers = 8;
    c.max_open_sockets = 4;
    c.lru_purge_enable = true;
    c.stack_size = 6144;
    c.task_priority = tskIDLE_PRIORITY + 2;     /* unter dem Sync-Task (3): Uploads gehen vor */
    httpd_handle_t srv = NULL;
    esp_err_t err = httpd_start(&srv, &c);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP-Server: %s", esp_err_to_name(err));
        return;
    }
    const httpd_uri_t uris[] = {
        { .uri = "/", .method = HTTP_GET, .handler = root_get },
        { .uri = "/scan", .method = HTTP_GET, .handler = scan_get },
        { .uri = "/save", .method = HTTP_POST, .handler = save_post },
        { .uri = "/restart", .method = HTTP_POST, .handler = restart_post },
        { .uri = "/setup", .method = HTTP_POST, .handler = setup_post },
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        httpd_register_uri_handler(srv, &uris[i]);
    }
    httpd_register_err_handler(srv, HTTPD_404_NOT_FOUND, error_handler);
    httpd_register_err_handler(srv, HTTPD_405_METHOD_NOT_ALLOWED, error_handler);
    ESP_LOGI(TAG, "Weboberfläche auf Port 80 (Benutzer " WEB_USER ")");
}
