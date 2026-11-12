/*
 * Firmware-Update per WLAN:
 *   GET {server}/api/v1/firmware/latest?project=<name>  -> 200 {"version","size","sha256","url"} | 404
 *   GET {server}{url}                                   -> Image (Bearer-Token), SHA-256 + Größe prüfen
 * Neue Firmware startet als "pending verify" und muss binnen CONFIG_AVX_OTA_VALIDATE_S den Server erreichen,
 * sonst Neustart und der Bootloader kehrt zur alten zurück. Der Token wird nie geloggt.
 */
#include "ota.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "nvs.h"
#include "psa/crypto.h"
#include "cJSON.h"

#include "led.h"
#include "report.h"
#include "wifi_sta.h"

static const char *TAG = "ota";

#define OTA_TIMEOUT_MS   20000
#define OTA_JSON_MAX     1024
#define OTA_BUF          4096
#define OTA_VER_MAX      32
#define OTA_URL_MAX      200
#define NVS_NS           "ota"

static app_config_t s_cfg;
static SemaphoreHandle_t s_busy;            /* Sync-Lauf oder Update-Prüfung */
static volatile bool s_pending;             /* frisch installiert, noch nicht bestätigt */
static volatile bool s_server_ok;
static volatile bool s_check_req;
static char s_rolled_back[OTA_VER_MAX];     /* Version der verworfenen Firmware, "" = keine */
static char s_failed_ver[OTA_VER_MAX];      /* zuletzt fehlgeschlagene Zielversion */
static int64_t s_failed_at;

static const char *running_version(void)
{
    const esp_app_desc_t *d = esp_app_get_description();
    return d ? d->version : "";
}

void ota_boot(bool configured)
{
    s_busy = xSemaphoreCreateMutex();
    const esp_partition_t *run = esp_ota_get_running_partition();
    esp_ota_img_states_t st;
    if (run && esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
        if (!configured) {
            ESP_LOGI(TAG, "Neue Firmware %s, Einrichtungsmodus: sofort gültig", running_version());
            esp_ota_mark_app_valid_cancel_rollback();
        } else {
            ESP_LOGW(TAG, "Neue Firmware %s: wird gültig, sobald der Server antwortet (Frist %d s)",
                     running_version(), CONFIG_AVX_OTA_VALIDATE_S);
            s_pending = true;
        }
    }

    /* Rollback erkannt? Einmal je verworfener Version melden (Merker im NVS). */
    const esp_partition_t *bad = esp_ota_get_last_invalid_partition();
    esp_app_desc_t bd;
    if (bad && esp_ota_get_partition_description(bad, &bd) == ESP_OK) {
        char sent[OTA_VER_MAX] = "";
        nvs_handle_t h;
        if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
            size_t len = sizeof(sent);
            nvs_get_str(h, "rb_sent", sent, &len);
            nvs_close(h);
        }
        if (strcmp(sent, bd.version) != 0) {
            ESP_LOGW(TAG, "Rollback: Firmware %s wurde verworfen, laufend %s", bd.version, running_version());
            strlcpy(s_rolled_back, bd.version, sizeof(s_rolled_back));
        }
    }
}

void ota_server_ok(void)
{
    s_server_ok = true;
    if (s_pending && wifi_sta_connected()) {
        if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
            ESP_LOGI(TAG, "Firmware %s bestätigt", running_version());
            s_pending = false;
        }
    }
}

void ota_sync_begin(void)
{
    if (s_busy) {
        xSemaphoreTake(s_busy, portMAX_DELAY);
    }
}

void ota_sync_end(void)
{
    if (s_busy) {
        xSemaphoreGive(s_busy);
    }
}

void ota_check_now(void)
{
    s_check_req = true;
}

/* ------------------------------------------------------------ HTTP */

static esp_http_client_handle_t open_get(const char *url, char *auth)
{
    esp_http_client_config_t hc = {
        .url = url,
        .method = HTTP_METHOD_GET,
        .timeout_ms = OTA_TIMEOUT_MS,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .disable_auto_redirect = true,
        .buffer_size = 2048,
    };
    esp_http_client_handle_t c = esp_http_client_init(&hc);
    if (!c) {
        return NULL;
    }
    esp_http_client_set_header(c, "Authorization", auth);
    esp_http_client_set_header(c, "X-Avinox-Device", report_device_id());
    if (esp_http_client_open(c, 0) != ESP_OK) {
        esp_http_client_cleanup(c);
        return NULL;
    }
    return c;
}

typedef struct {
    char version[OTA_VER_MAX];
    char url[OTA_URL_MAX];
    uint8_t sha[32];
    uint32_t size;
} fw_info_t;

static int hexval(char ch)
{
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

/* 1 = Info geholt, 0 = nichts da (404), -1 = Fehler */
static int fetch_latest(char *auth, fw_info_t *fw)
{
    char url[CFG_URL_MAX + 120];
    snprintf(url, sizeof(url), "%s/api/v1/firmware/latest?project=%s", s_cfg.url, esp_app_get_description()->project_name);
    esp_http_client_handle_t c = open_get(url, auth);
    if (!c) {
        ESP_LOGW(TAG, "Update-Prüfung: Server nicht erreichbar");
        return -1;
    }
    esp_http_client_fetch_headers(c);
    int st = esp_http_client_get_status_code(c);
    int rc = -1;
    char *buf = NULL;
    if (st == 404) {
        ota_server_ok();
        ESP_LOGI(TAG, "Keine Firmware auf dem Server");
        rc = 0;
        goto out;
    }
    if (st != 200) {
        ESP_LOGW(TAG, "Update-Prüfung: HTTP %d", st);
        goto out;
    }
    ota_server_ok();
    buf = malloc(OTA_JSON_MAX + 1);
    if (!buf) {
        goto out;
    }
    int len = 0, n;
    while (len < OTA_JSON_MAX && (n = esp_http_client_read(c, buf + len, OTA_JSON_MAX - len)) > 0) {
        len += n;
    }
    buf[len] = '\0';
    cJSON *o = cJSON_ParseWithLength(buf, len);
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, "version");
    const cJSON *u = cJSON_GetObjectItemCaseSensitive(o, "url");
    const cJSON *h = cJSON_GetObjectItemCaseSensitive(o, "sha256");
    const cJSON *s = cJSON_GetObjectItemCaseSensitive(o, "size");
    if (cJSON_IsString(v) && cJSON_IsString(u) && cJSON_IsString(h) && cJSON_IsNumber(s) &&
        strlen(v->valuestring) < sizeof(fw->version) && strlen(u->valuestring) < sizeof(fw->url) &&
        strlen(h->valuestring) == 64 && s->valuedouble > 0 && s->valuedouble < 0x1000000) {
        strlcpy(fw->version, v->valuestring, sizeof(fw->version));
        strlcpy(fw->url, u->valuestring, sizeof(fw->url));
        fw->size = (uint32_t)s->valuedouble;
        rc = 1;
        for (int i = 0; i < 32; i++) {
            int hi = hexval(h->valuestring[2 * i]), lo = hexval(h->valuestring[2 * i + 1]);
            if (hi < 0 || lo < 0) {
                rc = -1;
                break;
            }
            fw->sha[i] = (uint8_t)(hi << 4 | lo);
        }
    }
    if (rc != 1) {
        ESP_LOGW(TAG, "Update-Prüfung: ungültige Antwort");
    }
    cJSON_Delete(o);
out:
    free(buf);
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    return rc;
}

/* Lädt das Image in die freie OTA-Partition. ESP_OK = geprüft und als Boot-Partition gesetzt. */
static esp_err_t download(char *auth, const fw_info_t *fw, char *msg, size_t cap)
{
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (!part) {
        snprintf(msg, cap, "keine OTA-Partition");
        return ESP_FAIL;
    }
    if (fw->size > part->size) {
        snprintf(msg, cap, "Image zu groß (%" PRIu32 " > %" PRIu32 ")", fw->size, part->size);
        return ESP_FAIL;
    }
    char url[CFG_URL_MAX + OTA_URL_MAX];
    if (strncmp(fw->url, "http://", 7) == 0 || strncmp(fw->url, "https://", 8) == 0) {
        strlcpy(url, fw->url, sizeof(url));
    } else {
        snprintf(url, sizeof(url), "%s%s", s_cfg.url, fw->url);
    }
    esp_http_client_handle_t c = open_get(url, auth);
    if (!c) {
        snprintf(msg, cap, "Download: keine Verbindung");
        return ESP_FAIL;
    }
    int64_t clen = esp_http_client_fetch_headers(c);
    int st = esp_http_client_get_status_code(c);
    esp_err_t ret = ESP_FAIL;
    esp_ota_handle_t oh = 0;
    bool begun = false;
    uint8_t *buf = NULL;
    psa_hash_operation_t hop = PSA_HASH_OPERATION_INIT;
    bool hashing = false;

    if (st != 200) {
        snprintf(msg, cap, "Download: HTTP %d", st);
        goto out;
    }
    if (clen > 0 && clen != fw->size) {
        snprintf(msg, cap, "Download: Länge %" PRId64 " statt %" PRIu32, clen, fw->size);
        goto out;
    }
    buf = malloc(OTA_BUF);
    if (!buf) {
        snprintf(msg, cap, "kein Speicher");
        goto out;
    }
    if (psa_crypto_init() != PSA_SUCCESS || psa_hash_setup(&hop, PSA_ALG_SHA_256) != PSA_SUCCESS) {
        snprintf(msg, cap, "SHA-256 nicht verfügbar");
        goto out;
    }
    hashing = true;
    esp_err_t err = esp_ota_begin(part, fw->size, &oh);
    if (err != ESP_OK) {
        snprintf(msg, cap, "esp_ota_begin: %s", esp_err_to_name(err));
        goto out;
    }
    begun = true;
    ESP_LOGI(TAG, "Lade %s (%" PRIu32 " Byte) nach %s", fw->version, fw->size, part->label);
    uint32_t total = 0;
    while (total < fw->size) {
        int n = esp_http_client_read(c, (char *)buf, OTA_BUF);
        if (n < 0) {
            snprintf(msg, cap, "Download abgebrochen nach %" PRIu32 " Byte", total);
            goto out;
        }
        if (n == 0) {
            if (esp_http_client_is_complete_data_received(c)) {
                break;
            }
            snprintf(msg, cap, "Download abgebrochen nach %" PRIu32 " Byte", total);
            goto out;
        }
        if (total + (uint32_t)n > fw->size) {
            snprintf(msg, cap, "Download länger als %" PRIu32 " Byte", fw->size);
            goto out;
        }
        psa_hash_update(&hop, buf, n);
        err = esp_ota_write(oh, buf, n);
        if (err != ESP_OK) {
            snprintf(msg, cap, "esp_ota_write: %s", esp_err_to_name(err));
            goto out;
        }
        total += n;
    }
    if (total != fw->size) {
        snprintf(msg, cap, "Größe %" PRIu32 " statt %" PRIu32, total, fw->size);
        goto out;
    }
    uint8_t digest[32];
    size_t dlen = 0;
    hashing = false;
    if (psa_hash_finish(&hop, digest, sizeof(digest), &dlen) != PSA_SUCCESS || dlen != 32 ||
        memcmp(digest, fw->sha, 32) != 0) {
        snprintf(msg, cap, "SHA-256 stimmt nicht");
        goto out;
    }
    begun = false;
    err = esp_ota_end(oh);
    if (err != ESP_OK) {
        snprintf(msg, cap, "Image ungültig: %s", esp_err_to_name(err));
        goto out;
    }
    esp_app_desc_t nd;
    if (esp_ota_get_partition_description(part, &nd) != ESP_OK) {
        snprintf(msg, cap, "Beschreibung der neuen Firmware nicht lesbar");
        goto out;
    }
    if (strncmp(nd.project_name, esp_app_get_description()->project_name, sizeof(nd.project_name)) != 0) {
        snprintf(msg, cap, "falsches Projekt '%.32s'", nd.project_name);
        goto out;
    }
    err = esp_ota_set_boot_partition(part);
    if (err != ESP_OK) {
        snprintf(msg, cap, "esp_ota_set_boot_partition: %s", esp_err_to_name(err));
        goto out;
    }
    ret = ESP_OK;
out:
    if (begun) {
        esp_ota_abort(oh);
    }
    if (hashing) {
        psa_hash_abort(&hop);
    }
    free(buf);
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    return ret;
}

static void check_once(void)
{
    if (s_pending) {
        return;     /* erst bestätigen, dann weiter aktualisieren */
    }
    char auth[CFG_TOKEN_MAX + 16];
    snprintf(auth, sizeof(auth), "Bearer %s", s_cfg.token);
    fw_info_t fw;
    memset(&fw, 0, sizeof(fw));
    int rc = fetch_latest(auth, &fw);
    const char *cur = running_version();
    if (rc == 1 && strcmp(fw.version, cur) != 0) {
        bool recent_fail = strcmp(fw.version, s_failed_ver) == 0 &&
                           esp_timer_get_time() - s_failed_at < (int64_t)CONFIG_AVX_OTA_CHECK_S * 1000000;
        if (recent_fail) {
            ESP_LOGI(TAG, "Firmware %s zuletzt fehlgeschlagen, nächster Versuch beim nächsten Prüfzeitpunkt", fw.version);
        } else {
            ESP_LOGW(TAG, "Update %s -> %s", cur, fw.version);
            report_send_ota(&s_cfg, cur, fw.version, "started", NULL);
            led_set_override(LED_OTA);
            char msg[120] = "";
            if (download(auth, &fw, msg, sizeof(msg)) == ESP_OK) {
                report_send_ota(&s_cfg, cur, fw.version, "ok", NULL);
                ESP_LOGW(TAG, "Firmware %s installiert, Neustart", fw.version);
                memset(auth, 0, sizeof(auth));
                vTaskDelay(pdMS_TO_TICKS(500));
                esp_restart();
            }
            led_clear_override();
            ESP_LOGE(TAG, "Update auf %s fehlgeschlagen: %s", fw.version, msg);
            strlcpy(s_failed_ver, fw.version, sizeof(s_failed_ver));
            s_failed_at = esp_timer_get_time();
            report_send_ota(&s_cfg, cur, fw.version, "failed", msg);
        }
    } else if (rc == 1) {
        ESP_LOGI(TAG, "Firmware %s ist aktuell", cur);
    }
    memset(auth, 0, sizeof(auth));
}

static void ota_task(void *arg)
{
    const int64_t period = (int64_t)CONFIG_AVX_OTA_CHECK_S * 1000000;
    const int64_t deadline = (int64_t)CONFIG_AVX_OTA_VALIDATE_S * 1000000;
    int64_t next = -1;          /* erste Prüfung kurz nach der ersten WLAN-Verbindung */
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        int64_t now = esp_timer_get_time();
        if (s_pending && now >= deadline) {
            ESP_LOGE(TAG, "Neue Firmware hat den Server nicht erreicht: Neustart, Rückkehr zur vorherigen");
            esp_restart();
        }
        if (!wifi_sta_connected()) {
            continue;
        }
        if (s_rolled_back[0] && report_send_ota(&s_cfg, s_rolled_back, running_version(), "rolled_back", NULL)) {
            nvs_handle_t h;
            if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
                nvs_set_str(h, "rb_sent", s_rolled_back);
                nvs_commit(h);
                nvs_close(h);
            }
            s_rolled_back[0] = '\0';
        }
        if (next < 0) {
            next = now + 30 * 1000000LL;
        }
        if (!s_check_req && now < next) {
            continue;
        }
        if (xSemaphoreTake(s_busy, 0) != pdTRUE) {
            continue;   /* Sync läuft: später */
        }
        s_check_req = false;
        check_once();
        next = esp_timer_get_time() + period;
        xSemaphoreGive(s_busy);
    }
}

void ota_start(const app_config_t *cfg)
{
    s_cfg = *cfg;
    if (xTaskCreate(ota_task, "ota", 8192, NULL, 2, NULL) != pdPASS) {
        ESP_LOGE(TAG, "OTA-Task nicht gestartet");
        if (s_pending) {
            esp_ota_mark_app_valid_cancel_rollback();   /* ohne Task keine Frist: nicht ewig unbestätigt */
            s_pending = false;
        }
    }
}
