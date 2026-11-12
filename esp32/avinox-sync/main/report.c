/*
 * Rückmeldung an den Server: POST {server}/api/v1/device/report (Bearer-Token, JSON).
 * Heartbeat (event "heartbeat") und Sync-Bericht (event "sync"). Der Token wird nie geloggt.
 */
#include "report.h"

#include <stdio.h>
#include <string.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "cJSON.h"

#include "ota.h"
#include "usb_drive.h"
#include "wifi_sta.h"

static const char *TAG = "report";

#define REPORT_TIMEOUT_MS 10000

static char s_id[24];
static SemaphoreHandle_t s_lock;
static app_config_t s_cfg;

const char *report_device_id(void)
{
    if (!s_id[0]) {
        uint8_t mac[6] = { 0 };
        esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
        snprintf(s_id, sizeof(s_id), "avinox-sync-%02X%02X", mac[4], mac[5]);
    }
    return s_id;
}

static const char *drive_str(void)
{
    switch (usb_drive_state()) {
    case DRIVE_READY:  return "present";
    case DRIVE_FAILED: return "error";
    default:           return "absent";
    }
}

static char *build_body(const sync_result_t *res)
{
    cJSON *o = cJSON_CreateObject();
    if (!o) {
        return NULL;
    }
    cJSON_AddStringToObject(o, "device_id", report_device_id());
    cJSON_AddStringToObject(o, "event", res ? "sync" : "heartbeat");
    const esp_app_desc_t *d = esp_app_get_description();
    cJSON_AddStringToObject(o, "firmware", d ? d->version : "");
    cJSON_AddNumberToObject(o, "uptime_s", (double)(esp_timer_get_time() / 1000000));
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        cJSON_AddNumberToObject(o, "rssi", ap.rssi);
    }
    esp_netif_t *nif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip;
    if (nif && esp_netif_get_ip_info(nif, &ip) == ESP_OK && ip.ip.addr) {
        char s[16];
        snprintf(s, sizeof(s), IPSTR, IP2STR(&ip.ip));
        cJSON_AddStringToObject(o, "ip", s);
    }
    cJSON_AddNumberToObject(o, "free_heap", (double)esp_get_free_heap_size());
    cJSON_AddStringToObject(o, "drive", drive_str());
    if (res) {
        cJSON *s = cJSON_AddObjectToObject(o, "sync");
        if (s) {
            cJSON_AddNumberToObject(s, "checked", res->found);
            cJSON_AddNumberToObject(s, "new", res->uploaded);
            cJSON_AddNumberToObject(s, "skipped", res->skipped);
            cJSON_AddNumberToObject(s, "failed", res->errors);
            cJSON_AddNumberToObject(s, "bytes", (double)res->bytes);
            cJSON_AddNumberToObject(s, "duration_ms", res->duration_ms);
            cJSON_AddStringToObject(s, "result", !res->ok ? "error" : (res->msg[0] ? "warn" : "ok"));
            cJSON_AddStringToObject(s, "error", res->msg);
        }
    }
    char *txt = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    return txt;
}

static int post_report(const app_config_t *cfg, const char *body);

bool report_send_ota(const app_config_t *cfg, const char *from, const char *to, const char *result, const char *error)
{
    cJSON *o = cJSON_CreateObject();
    if (!o) {
        return false;
    }
    cJSON_AddStringToObject(o, "device_id", report_device_id());
    cJSON_AddStringToObject(o, "event", "ota");
    const esp_app_desc_t *d = esp_app_get_description();
    cJSON_AddStringToObject(o, "firmware", d ? d->version : "");
    cJSON_AddNumberToObject(o, "uptime_s", (double)(esp_timer_get_time() / 1000000));
    cJSON *ota = cJSON_AddObjectToObject(o, "ota");
    if (ota) {
        cJSON_AddStringToObject(ota, "from", from ? from : "");
        cJSON_AddStringToObject(ota, "to", to ? to : "");
        cJSON_AddStringToObject(ota, "result", result);
        if (error && error[0]) {
            cJSON_AddStringToObject(ota, "error", error);
        }
    }
    char *body = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (!body) {
        return false;
    }
    if (!s_lock) {
        s_lock = xSemaphoreCreateMutex();
    }
    bool ok = false;
    if (s_lock && xSemaphoreTake(s_lock, pdMS_TO_TICKS(12000)) == pdTRUE) {
        int st = post_report(cfg, body);
        xSemaphoreGive(s_lock);
        ok = st >= 200 && st < 300;
        if (ok) {
            ESP_LOGI(TAG, "Bericht (ota %s) gesendet", result);
            ota_server_ok();
        } else {
            ESP_LOGW(TAG, "Bericht (ota %s) nicht gesendet (HTTP %d)", result, st);
        }
    }
    free(body);
    return ok;
}

/* Sendet body, liefert HTTP-Status oder -1 bei Netzfehler. Aufrufer hält s_lock. */
static int post_report(const app_config_t *cfg, const char *body)
{
    char url[CFG_URL_MAX + 40];
    snprintf(url, sizeof(url), "%s/api/v1/device/report", cfg->url);
    char auth[CFG_TOKEN_MAX + 16];
    snprintf(auth, sizeof(auth), "Bearer %s", cfg->token);
    esp_http_client_config_t hc = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = REPORT_TIMEOUT_MS,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .disable_auto_redirect = true,
    };
    int st = -1;
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (c) {
        esp_http_client_set_header(c, "Authorization", auth);
        esp_http_client_set_header(c, "X-Avinox-Device", report_device_id());
        esp_http_client_set_header(c, "Content-Type", "application/json");
        esp_http_client_set_post_field(c, body, strlen(body));
        if (esp_http_client_perform(c) == ESP_OK) {
            st = esp_http_client_get_status_code(c);
        }
        esp_http_client_cleanup(c);
    }
    memset(auth, 0, sizeof(auth));
    return st;
}

void report_send(const app_config_t *cfg, const sync_result_t *res)
{
    if (!s_lock) {
        s_lock = xSemaphoreCreateMutex();
    }
    /* Sync-Bericht wartet kurz, Heartbeat überspringt, wenn gerade ein Bericht läuft. */
    if (!s_lock || xSemaphoreTake(s_lock, pdMS_TO_TICKS(res ? 12000 : 0)) != pdTRUE) {
        return;
    }
    char *body = build_body(res);
    if (!body) {
        ESP_LOGW(TAG, "Bericht: kein Speicher");
        xSemaphoreGive(s_lock);
        return;
    }
    const char *kind = res ? "sync" : "heartbeat";
    int st = post_report(cfg, body);
    if (st < 0) {
        ESP_LOGW(TAG, "Bericht (%s) nicht gesendet", kind);
    } else if (st >= 200 && st < 300) {
        ESP_LOGI(TAG, "Bericht (%s) gesendet", kind);
        ota_server_ok();
    } else {
        ESP_LOGW(TAG, "Bericht (%s): HTTP %d", kind, st);
    }
    free(body);
    xSemaphoreGive(s_lock);
}

static void heartbeat_task(void *arg)
{
    uint32_t seen = 0;
    int64_t next = 0;
    const int64_t period = (int64_t)CONFIG_AVX_HEARTBEAT_S * 1000000;
    while (true) {
        if (wifi_sta_connected()) {
            uint32_t g = wifi_sta_generation();
            if (g != seen || esp_timer_get_time() >= next) {
                seen = g;
                report_send(&s_cfg, NULL);
                next = esp_timer_get_time() + period;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void report_start_heartbeat(const app_config_t *cfg)
{
    s_cfg = *cfg;
    if (xTaskCreate(heartbeat_task, "heartbeat", 8192, NULL, 2, NULL) != pdPASS) {
        ESP_LOGW(TAG, "Heartbeat-Task nicht gestartet");
    }
}
