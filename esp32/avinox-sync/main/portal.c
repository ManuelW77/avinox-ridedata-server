#include "portal.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_mac.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "lwip/sockets.h"

#include "led.h"
#include "sync.h"
#include "web_common.h"

static const char *TAG = "portal";

#define AP_IP_STR       "192.168.4.1"
#define MAX_APS         20
#define RESCUE_TIMEOUT_US (15LL * 60 * 1000000)

static app_config_t s_cfg;
static char s_ap_ssid[33];
static wifi_ap_record_t s_aps[MAX_APS];
static uint16_t s_ap_count;

/* ------------------------------------------------------------ Seiten */

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

static esp_err_t root_get(httpd_req_t *req)
{
    web_detect_lang(req);
    buf_t b;
    buf_init(&b);
    web_page_head(&b, "/");
    web_render_rescue_banner(&b);
    status_block(&b);
    web_render_form(&b, &s_cfg, s_aps, s_ap_count, true);
    web_page_tail(&b);
    return buf_send(req, &b);
}


static esp_err_t status_get(httpd_req_t *req)
{
    web_detect_lang(req);
    buf_t b;
    buf_init(&b);
    web_page_head(&b, "/status");
    web_render_rescue_banner(&b);
    status_block(&b);
    buf_append(&b, "<p><a href=\"/\">");
    buf_append(&b, T(T_TO_SETUP));
    buf_append(&b, "</a></p>");
    web_page_tail(&b);
    return buf_send(req, &b);
}

static void do_scan(void)
{
    int n = web_scan(s_aps, MAX_APS);
    if (n >= 0) {
        s_ap_count = n;
    }
}


static esp_err_t scan_get(httpd_req_t *req)
{
    do_scan();
    httpd_resp_set_status(req, "303 See Other");
    httpd_resp_set_hdr(req, "Location", "/");
    return httpd_resp_send(req, NULL, 0);
}

/* ---- Verbindung prüfen: Station für die Prüfung kurz mit dem eingetragenen WLAN verbinden */

#define PROBE_WIFI_MS   15000
#define BIT_GOT_IP      BIT0
#define BIT_FAILED      BIT1

static EventGroupHandle_t s_wev;
static volatile bool s_probing;
static volatile int s_fail_reason;

static void probe_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (!s_probing) {
        return;
    }
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_fail_reason = ((const wifi_event_sta_disconnected_t *)data)->reason;
        xEventGroupSetBits(s_wev, BIT_FAILED);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_wev, BIT_GOT_IP);
    }
}

static web_wifi_result_t probe_connect(const char *ssid, const char *pass, int *reason)
{
    if (!s_wev) {
        s_wev = xEventGroupCreate();
        if (!s_wev ||
            esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, probe_event, NULL) != ESP_OK ||
            esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, probe_event, NULL) != ESP_OK) {
            return WIFI_PROBE_FAIL;
        }
    }
    wifi_config_t wc = { 0 };
    strlcpy((char *)wc.sta.ssid, ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, pass, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = pass[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    wc.sta.pmf_cfg.capable = true;
    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wc);
    memset(&wc, 0, sizeof(wc));
    if (err != ESP_OK) {
        *reason = err;
        return WIFI_PROBE_FAIL;
    }
    xEventGroupClearBits(s_wev, BIT_GOT_IP | BIT_FAILED);
    s_fail_reason = 0;
    s_probing = true;
    err = esp_wifi_connect();
    if (err != ESP_OK) {
        *reason = err;
        return WIFI_PROBE_FAIL;
    }
    EventBits_t bits = xEventGroupWaitBits(s_wev, BIT_GOT_IP | BIT_FAILED, pdTRUE, pdFALSE, pdMS_TO_TICKS(PROBE_WIFI_MS));
    if (bits & BIT_GOT_IP) {
        return WIFI_PROBE_OK;
    }
    if (!(bits & BIT_FAILED)) {
        return WIFI_PROBE_TIMEOUT;
    }
    *reason = s_fail_reason;
    ESP_LOGI(TAG, "WLAN-Prüfung fehlgeschlagen, Grund %d", s_fail_reason);
    switch (s_fail_reason) {
    case WIFI_REASON_NO_AP_FOUND:
        return WIFI_PROBE_NOT_FOUND;
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_MIC_FAILURE:
        return WIFI_PROBE_AUTH;
    default:
        return WIFI_PROBE_FAIL;
    }
}

static void probe_release(void)
{
    s_probing = false;
    esp_wifi_disconnect();      /* Portal läuft danach wie vorher (Station getrennt) */
}

static const web_wifi_probe_t s_probe = { .connect = probe_connect, .release = probe_release };

static esp_err_t check_post(httpd_req_t *req)
{
    return web_handle_check(req, &s_cfg, &s_probe);
}

static esp_err_t save_post(httpd_req_t *req)
{
    return web_handle_save(req, &s_cfg, true);
}


static esp_err_t restart_post(httpd_req_t *req)
{
    esp_err_t err = web_send_message(req, "ok", T_OK_RESTART, false);
    web_schedule_restart();
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
    web_detect_lang(req);
    char page[200];
    snprintf(page, sizeof(page), "<!DOCTYPE html><html lang=\"%s\"><body><a href=\"http://" AP_IP_STR "/\">%s</a></body></html>",
             web_lang() ? "en" : "de", T(T_CAPTIVE));
    return httpd_resp_sendstr(req, page);
}

static void start_http(void)
{
    httpd_config_t c = HTTPD_DEFAULT_CONFIG();
    c.max_uri_handlers = 20;
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
        { .uri = "/check", .method = HTTP_POST, .handler = check_post },
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

void portal_run(bool rescue)
{
    web_set_rescue(rescue);
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
    if (!rescue && strlen(s_cfg.ap_pass) >= 8) {
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
    if (rescue) {
        ESP_LOGW(TAG, "NOTFALLMODUS (BOOT-Taste): Einrichtungs-WLAN '%s' OFFEN, neues Zugangspasswort erforderlich, "
                 "Ende nach 15 min ohne Speichern", s_ap_ssid);
    }
    ESP_LOGI(TAG, "Einrichtungsmodus: WLAN '%s' (%s), dann http://" AP_IP_STR "/",
             s_ap_ssid, ap.ap.authmode == WIFI_AUTH_OPEN ? "offen" : "WPA2");

    do_scan();
    start_http();
    if (xTaskCreate(dns_task, "dns", 3072, NULL, 4, NULL) != pdPASS) {
        ESP_LOGW(TAG, "DNS-Task nicht gestartet (kein Captive Portal)");
    }
    int64_t start_us = esp_timer_get_time();
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        if (rescue && config_complete(&s_cfg) && esp_timer_get_time() - start_us >= RESCUE_TIMEOUT_US) {
            ESP_LOGW(TAG, "Notfallmodus: 15 min ohne Speichern, Neustart in den Normalbetrieb");
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
        }
    }
}
