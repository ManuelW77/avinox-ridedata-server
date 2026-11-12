#include "wifi_sta.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_timer.h"

static const char *TAG = "wifi";

#define CONNECT_WINDOW_MS   30000   /* so lange wird am Stück versucht */
#define RETRY_PAUSE_MS      60000   /* danach Pause bis zum nächsten Versuch */

#define BIT_CONNECTED   BIT0
#define BIT_LOST        BIT1

static EventGroupHandle_t s_events;
static volatile bool s_trying;          /* innerhalb des 30-s-Fensters */
static volatile uint32_t s_generation;

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = data;
        bool was_connected = xEventGroupGetBits(s_events) & BIT_CONNECTED;
        xEventGroupClearBits(s_events, BIT_CONNECTED);
        if (was_connected) {
            ESP_LOGW(TAG, "WLAN-Verbindung verloren (Grund %d)", d->reason);
            xEventGroupSetBits(s_events, BIT_LOST);
        } else if (s_trying) {
            ESP_LOGD(TAG, "Verbindungsversuch gescheitert (Grund %d), neuer Versuch", d->reason);
            vTaskDelay(pdMS_TO_TICKS(1000));   /* Event-Task kurz bremsen, sonst Dauerschleife */
            if (s_trying) {
                esp_wifi_connect();
            }
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "Verbunden, IP " IPSTR, IP2STR(&e->ip_info.ip));
        s_generation++;
        xEventGroupSetBits(s_events, BIT_CONNECTED);
    }
}

static void wifi_task(void *arg)
{
    while (true) {
        EventBits_t bits = xEventGroupGetBits(s_events);
        if (bits & BIT_CONNECTED) {
            xEventGroupWaitBits(s_events, BIT_LOST, pdTRUE, pdFALSE, portMAX_DELAY);
            continue;   /* Verbindung weg: sofort neues 30-s-Fenster */
        }
        xEventGroupClearBits(s_events, BIT_LOST);
        ESP_LOGI(TAG, "Verbinde mit WLAN ...");
        s_trying = true;
        esp_err_t err = esp_wifi_connect();
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "esp_wifi_connect: %s", esp_err_to_name(err));
        }
        bits = xEventGroupWaitBits(s_events, BIT_CONNECTED, pdFALSE, pdFALSE, pdMS_TO_TICKS(CONNECT_WINDOW_MS));
        s_trying = false;
        if (bits & BIT_CONNECTED) {
            continue;
        }
        ESP_LOGW(TAG, "Kein WLAN nach %d s, nächster Versuch in %d s", CONNECT_WINDOW_MS / 1000, RETRY_PAUSE_MS / 1000);
        esp_wifi_disconnect();
        vTaskDelay(pdMS_TO_TICKS(RETRY_PAUSE_MS));
    }
}

esp_err_t wifi_sta_start(const app_config_t *cfg)
{
    s_events = xEventGroupCreate();
    if (!s_events) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_netif_init();
    if (err == ESP_OK) err = esp_event_loop_create_default();
    if (err == ESP_ERR_INVALID_STATE) err = ESP_OK;   /* Loop existiert schon */
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Netif/Event-Loop: %s", esp_err_to_name(err));
        return err;
    }
    if (!esp_netif_create_default_wifi_sta()) {
        return ESP_FAIL;
    }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&init);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_init: %s", esp_err_to_name(err));
        return err;
    }
    esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, on_event, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL);

    wifi_config_t wc = { 0 };
    strlcpy((char *)wc.sta.ssid, cfg->ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, cfg->pass, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = cfg->pass[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    wc.sta.pmf_cfg.capable = true;

    err = esp_wifi_set_storage(WIFI_STORAGE_RAM);
    if (err == ESP_OK) err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err == ESP_OK) err = esp_wifi_set_config(WIFI_IF_STA, &wc);
    if (err == ESP_OK) err = esp_wifi_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "WLAN-Start: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "WLAN-Station gestartet, SSID '%s'", cfg->ssid);
    if (xTaskCreate(wifi_task, "wifi_sta", 3072, NULL, 3, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool wifi_sta_connected(void)
{
    return s_events && (xEventGroupGetBits(s_events) & BIT_CONNECTED);
}

uint32_t wifi_sta_generation(void)
{
    return s_generation;
}
