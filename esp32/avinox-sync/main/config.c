#include "config.h"

#include <string.h>
#include <strings.h>
#include <ctype.h>
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"

static const char *TAG = "config";

#define NS          "cfg"
#define K_SSID      "wifi_ssid"
#define K_PASS      "wifi_pass"
#define K_URL       "server_url"
#define K_TOKEN     "token"
#define K_APPASS    "ap_pass"
#define K_PORTAL    "portal"

esp_err_t config_nvs_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS wird neu angelegt (%s)", esp_err_to_name(err));
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS-Init fehlgeschlagen: %s", esp_err_to_name(err));
    }
    return err;
}

static void get_str(nvs_handle_t h, const char *key, char *out, size_t cap)
{
    size_t n = cap;
    out[0] = '\0';
    esp_err_t err = nvs_get_str(h, key, out, &n);
    if (err != ESP_OK) {
        out[0] = '\0';
        if (err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "Schlüssel %s nicht lesbar: %s", key, esp_err_to_name(err));
        }
    }
}

esp_err_t config_load(app_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &h);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;      /* noch nie gespeichert */
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open(%s): %s", NS, esp_err_to_name(err));
        return err;
    }
    get_str(h, K_SSID, cfg->ssid, sizeof(cfg->ssid));
    get_str(h, K_PASS, cfg->pass, sizeof(cfg->pass));
    get_str(h, K_URL, cfg->url, sizeof(cfg->url));
    get_str(h, K_TOKEN, cfg->token, sizeof(cfg->token));
    get_str(h, K_APPASS, cfg->ap_pass, sizeof(cfg->ap_pass));
    nvs_close(h);
    config_normalize_url(cfg->url);
    return ESP_OK;
}

bool config_complete(const app_config_t *cfg)
{
    return cfg->ssid[0] && cfg->url[0] && cfg->token[0];
}

esp_err_t config_save(const app_config_t *cfg)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open(%s): %s", NS, esp_err_to_name(err));
        return err;
    }
    if (err == ESP_OK) err = nvs_set_str(h, K_SSID, cfg->ssid);
    if (err == ESP_OK) err = nvs_set_str(h, K_PASS, cfg->pass);
    if (err == ESP_OK) err = nvs_set_str(h, K_URL, cfg->url);
    if (err == ESP_OK) err = nvs_set_str(h, K_TOKEN, cfg->token);
    if (err == ESP_OK) err = nvs_set_str(h, K_APPASS, cfg->ap_pass);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Speichern fehlgeschlagen: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "Konfiguration gespeichert (SSID '%s', Server %s)", cfg->ssid, cfg->url);
    }
    return err;
}

esp_err_t config_factory_reset(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_erase_all(h);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

esp_err_t config_request_portal(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_u8(h, K_PORTAL, 1);
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    return err;
}

bool config_take_portal_request(void)
{
    nvs_handle_t h;
    uint8_t v = 0;
    if (nvs_open(NS, NVS_READWRITE, &h) != ESP_OK) {
        return false;
    }
    if (nvs_get_u8(h, K_PORTAL, &v) == ESP_OK) {
        nvs_erase_key(h, K_PORTAL);
        nvs_commit(h);
    }
    nvs_close(h);
    return v != 0;
}

bool config_normalize_url(char *url)
{
    /* führende/abschließende Leerzeichen und abschließende '/' entfernen */
    size_t start = 0;
    while (url[start] && isspace((unsigned char)url[start])) {
        start++;
    }
    if (start) {
        memmove(url, url + start, strlen(url + start) + 1);
    }
    size_t n = strlen(url);
    while (n > 0 && (isspace((unsigned char)url[n - 1]) || url[n - 1] == '/')) {
        url[--n] = '\0';
    }
    return (strncasecmp(url, "http://", 7) == 0 && n > 7) || (strncasecmp(url, "https://", 8) == 0 && n > 8);
}
