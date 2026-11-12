/*
 * Sync mit dem Server (API: server/src/avinox/server/app.py, Upload-Teil in store.py):
 *   GET  /api/v1/manifest                          -> {"files":[{"path","size"}], "patterns":{...}}
 *   GET  /api/v1/upload/status?path=&size=         -> {"received", "complete"}
 *   PUT  /api/v1/upload?path=&size=&offset=        -> 200 {"received","complete"} | 409 {"received",..}
 * Alle mit "Authorization: Bearer <token>". Der Token wird nie geloggt.
 */
#include "sync.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include <dirent.h>
#include <sys/stat.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "nvs.h"
#include "cJSON.h"

#include "report.h"
#include "sync_paths.h"
#include "usb_drive.h"
#include "wifi_sta.h"

static const char *TAG = "sync";

#define NVS_NS          "sync"
#define NVS_KEY_LAST    "last"
#define NVS_KEY_BOOTS   "boots"

#define REL_MAX         128             /* Pfad relativ zur Laufwerkswurzel */
#define URL_MAX         (CFG_URL_MAX + 3 * REL_MAX + 96)
#define MANIFEST_MAX    (256 * 1024)    /* Obergrenze für die Manifest-Antwort */
#define SMALL_RESP_MAX  1024
#define MAX_RETRIES     3
#define MAX_CONFLICTS   5
#define MAX_CANDIDATES  512
#define HTTP_TIMEOUT_MS 20000

static const int s_retry_pause_ms[MAX_RETRIES] = { 2000, 5000, 10000 };
static uint32_t s_boot;

/* ------------------------------------------------------------ HTTP */

typedef struct {
    esp_http_client_handle_t client;
    char *buf;
    size_t len;
    size_t cap;
    size_t max;
    bool overflow;
} http_ctx_t;

static esp_err_t http_event(esp_http_client_event_t *evt)
{
    http_ctx_t *ctx = evt->user_data;
    if (evt->event_id != HTTP_EVENT_ON_DATA || !ctx || evt->data_len <= 0) {
        return ESP_OK;
    }
    size_t need = ctx->len + (size_t)evt->data_len + 1;
    if (need > ctx->max) {
        ctx->overflow = true;
        return ESP_OK;
    }
    if (need > ctx->cap) {
        size_t ncap = ctx->cap ? ctx->cap : 1024;
        while (ncap < need) {
            ncap *= 2;
        }
        if (ncap > ctx->max) {
            ncap = ctx->max;
        }
        char *n = realloc(ctx->buf, ncap);
        if (!n) {
            ctx->overflow = true;
            return ESP_OK;
        }
        ctx->buf = n;
        ctx->cap = ncap;
    }
    memcpy(ctx->buf + ctx->len, evt->data, evt->data_len);
    ctx->len += evt->data_len;
    ctx->buf[ctx->len] = '\0';
    return ESP_OK;
}

static bool online(void)
{
    return wifi_sta_connected() && usb_drive_state() == DRIVE_READY;
}

/* Eine Anfrage. ESP_OK heißt: Antwort erhalten (Status in *status, Text in ctx->buf). */
static esp_err_t http_once(http_ctx_t *ctx, esp_http_client_method_t method, const char *url,
                           const char *body, int body_len, size_t resp_max, int *status)
{
    ctx->len = 0;
    ctx->overflow = false;
    ctx->max = resp_max;
    if (ctx->buf) {
        ctx->buf[0] = '\0';
    }
    esp_http_client_handle_t c = ctx->client;
    esp_http_client_set_url(c, url);
    esp_http_client_set_method(c, method);
    if (body) {
        esp_http_client_set_header(c, "Content-Type", "application/octet-stream");
        esp_http_client_set_post_field(c, body, body_len);
    } else {
        esp_http_client_set_post_field(c, NULL, 0);
    }
    esp_err_t err = esp_http_client_perform(c);
    if (err != ESP_OK) {
        esp_http_client_close(c);   /* nächster Versuch mit frischer Verbindung */
        return err;
    }
    *status = esp_http_client_get_status_code(c);
    if (ctx->overflow) {
        ESP_LOGW(TAG, "Antwort zu groß oder kein Speicher (> %u Byte)", (unsigned)resp_max);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

/* Mit Wiederholungen bei Netzfehlern und 5xx. Andere Statuscodes gehen direkt an den Aufrufer. */
static esp_err_t http_retry(http_ctx_t *ctx, esp_http_client_method_t method, const char *url,
                            const char *body, int body_len, size_t resp_max, int *status)
{
    esp_err_t err = ESP_FAIL;
    for (int attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        if (attempt > 0) {
            ESP_LOGW(TAG, "Wiederholung %d/%d in %d s", attempt, MAX_RETRIES, s_retry_pause_ms[attempt - 1] / 1000);
            vTaskDelay(pdMS_TO_TICKS(s_retry_pause_ms[attempt - 1]));
        }
        if (!online()) {
            return ESP_ERR_INVALID_STATE;
        }
        *status = 0;
        err = http_once(ctx, method, url, body, body_len, resp_max, status);
        if (err == ESP_ERR_NO_MEM) {
            return err;
        }
        if (err == ESP_OK && *status < 500) {
            return ESP_OK;
        }
        if (err == ESP_OK) {
            ESP_LOGW(TAG, "Server antwortet HTTP %d", *status);
            err = ESP_FAIL;
        } else {
            ESP_LOGW(TAG, "Netzfehler: %s", esp_err_to_name(err));
        }
    }
    return err;
}

/* ------------------------------------------------------------ Manifest */

static esp_err_t fetch_manifest(http_ctx_t *ctx, const char *base, avx_manifest_t *m, char *msg, size_t msg_cap)
{
    char url[URL_MAX];
    snprintf(url, sizeof(url), "%s/api/v1/manifest", base);
    int status = 0;
    esp_err_t err = http_retry(ctx, HTTP_METHOD_GET, url, NULL, 0, MANIFEST_MAX, &status);
    if (err != ESP_OK) {
        snprintf(msg, msg_cap, "Manifest: %s", esp_err_to_name(err));
        return err;
    }
    if (status != 200) {
        snprintf(msg, msg_cap, "Manifest: HTTP %d%s", status,
                 (status == 401 || status == 403) ? " (Token falsch?)" : "");
        return ESP_FAIL;
    }
    cJSON *root = cJSON_ParseWithLength(ctx->buf, ctx->len);
    cJSON *files = root ? cJSON_GetObjectItemCaseSensitive(root, "files") : NULL;
    if (!cJSON_IsArray(files)) {
        cJSON_Delete(root);
        snprintf(msg, msg_cap, "Manifest: ungültiges JSON");
        return ESP_FAIL;
    }
    const cJSON *f;
    cJSON_ArrayForEach(f, files) {
        const cJSON *p = cJSON_GetObjectItemCaseSensitive(f, "path");
        const cJSON *s = cJSON_GetObjectItemCaseSensitive(f, "size");
        if (cJSON_IsString(p) && cJSON_IsNumber(s) && s->valuedouble >= 0) {
            if (!avx_manifest_add(m, p->valuestring, (uint64_t)s->valuedouble)) {
                cJSON_Delete(root);
                snprintf(msg, msg_cap, "Manifest: kein Speicher");
                return ESP_ERR_NO_MEM;
            }
        }
    }
    const cJSON *patterns = cJSON_GetObjectItemCaseSensitive(root, "patterns");
    ESP_LOGI(TAG, "Manifest: %u bekannte Dateien, %d Muster", (unsigned)m->count,
             cJSON_IsObject(patterns) ? cJSON_GetArraySize(patterns) : 0);
    cJSON_Delete(root);
    return ESP_OK;
}

/* ------------------------------------------------------------ Laufwerk durchsuchen */

typedef struct {
    char rel[REL_MAX];
    uint64_t size;
} candidate_t;

typedef struct {
    candidate_t *items;
    size_t count;
    size_t cap;
} cand_list_t;

static void cand_add(cand_list_t *l, const char *rel)
{
    if (!avx_path_allowed(rel)) {
        return;
    }
    if (l->count >= MAX_CANDIDATES) {
        ESP_LOGW(TAG, "Mehr als %d passende Dateien, Rest beim nächsten Mal", MAX_CANDIDATES);
        return;
    }
    char full[REL_MAX + sizeof(USB_MNT_PATH) + 1];
    snprintf(full, sizeof(full), USB_MNT_PATH "/%s", rel);
    struct stat st;
    if (stat(full, &st) != 0 || !S_ISREG(st.st_mode)) {
        return;
    }
    if (l->count == l->cap) {
        size_t ncap = l->cap ? l->cap * 2 : 16;
        candidate_t *n = realloc(l->items, ncap * sizeof(*n));
        if (!n) {
            ESP_LOGE(TAG, "Kein Speicher für Dateiliste");
            return;
        }
        l->items = n;
        l->cap = ncap;
    }
    strlcpy(l->items[l->count].rel, rel, REL_MAX);
    l->items[l->count].size = (uint64_t)st.st_size;
    l->count++;
}

/* Dateien in einem Ordner (relativ), optional eine Ebene Unterordner. Nur ride_rec/ wird gelistet. */
static void scan_dir(cand_list_t *l, const char *reldir, bool subdirs)
{
    char full[REL_MAX + sizeof(USB_MNT_PATH) + 1];
    snprintf(full, sizeof(full), USB_MNT_PATH "/%s", reldir);
    DIR *dh = opendir(full);
    if (!dh) {
        ESP_LOGI(TAG, "%s/ nicht vorhanden", reldir);
        return;
    }
    struct dirent *d;
    char rel[REL_MAX];
    while ((d = readdir(dh)) != NULL) {
        int w = snprintf(rel, sizeof(rel), "%s/%s", reldir, d->d_name);
        if (w < 0 || (size_t)w >= sizeof(rel)) {
            continue;   /* Name zu lang, passt ohnehin zu keinem Muster */
        }
        if (d->d_type == DT_DIR) {
            if (subdirs && avx_is_segment(d->d_name)) {
                scan_dir(l, rel, false);
            }
        } else if (avx_is_ride_name(d->d_name) || avx_is_index_name(d->d_name)) {
            cand_add(l, rel);
        }
    }
    closedir(dh);
}

static void scan_drive(cand_list_t *l)
{
    scan_dir(l, "ride_rec", true);
    cand_add(l, "buried_rec/buried_file_cur.json");
    cand_add(l, "hms_rec/hms_current_storage.proto");
}

/* ------------------------------------------------------------ Upload */

typedef enum { UP_DONE, UP_FAILED, UP_ABORT } up_result_t;

static bool parse_progress(const char *json, size_t len, uint64_t *received, bool *complete)
{
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (!root) {
        return false;
    }
    const cJSON *r = cJSON_GetObjectItemCaseSensitive(root, "received");
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(root, "complete");
    bool ok = cJSON_IsNumber(r) && r->valuedouble >= 0;
    if (ok) {
        *received = (uint64_t)r->valuedouble;
        *complete = cJSON_IsTrue(c);
    }
    cJSON_Delete(root);
    return ok;
}

static up_result_t upload_file(http_ctx_t *ctx, const char *base, const candidate_t *cand,
                               uint8_t *chunk, sync_result_t *res, char *msg, size_t msg_cap)
{
    char enc[3 * REL_MAX + 1];
    if (!avx_url_encode(cand->rel, enc, sizeof(enc))) {
        return UP_FAILED;
    }
    char url[URL_MAX];
    int status = 0;
    uint64_t received = 0;
    bool complete = false;

    snprintf(url, sizeof(url), "%s/api/v1/upload/status?path=%s&size=%" PRIu64, base, enc, cand->size);
    esp_err_t err = http_retry(ctx, HTTP_METHOD_GET, url, NULL, 0, SMALL_RESP_MAX, &status);
    if (err == ESP_ERR_INVALID_STATE) {
        return UP_ABORT;
    }
    if (err != ESP_OK || status != 200 || !parse_progress(ctx->buf, ctx->len, &received, &complete)) {
        ESP_LOGW(TAG, "%s: Status nicht abrufbar (%s, HTTP %d)", cand->rel, esp_err_to_name(err), status);
        if (status == 401 || status == 403) {
            snprintf(msg, msg_cap, "Token abgelehnt (HTTP %d)", status);
            return UP_ABORT;
        }
        return UP_FAILED;
    }
    if (complete) {
        ESP_LOGI(TAG, "%s: schon vollständig auf dem Server", cand->rel);
        res->skipped++;
        return UP_DONE;
    }
    if (received > cand->size) {
        ESP_LOGW(TAG, "%s: Server meldet %" PRIu64 " > %" PRIu64 " Byte", cand->rel, received, cand->size);
        return UP_FAILED;
    }

    char full[REL_MAX + sizeof(USB_MNT_PATH) + 1];
    snprintf(full, sizeof(full), USB_MNT_PATH "/%s", cand->rel);
    FILE *f = fopen(full, "rb");     /* nur lesen */
    if (!f) {
        ESP_LOGW(TAG, "%s: nicht lesbar", cand->rel);
        return UP_FAILED;
    }
    ESP_LOGI(TAG, "%s: lade ab %" PRIu64 " von %" PRIu64 " Byte hoch", cand->rel, received, cand->size);
    int64_t t0 = esp_timer_get_time();
    uint64_t offset = received;
    uint64_t file_pos = UINT64_MAX;
    int conflicts = 0;
    up_result_t result = UP_FAILED;

    while (!complete) {
        if (offset >= cand->size) {
            ESP_LOGW(TAG, "%s: Server meldet nicht vollständig, obwohl alles gesendet ist", cand->rel);
            break;
        }
        if (!online()) {
            result = UP_ABORT;
            break;
        }
        size_t n = cand->size - offset < CONFIG_AVX_CHUNK_SIZE ? (size_t)(cand->size - offset) : CONFIG_AVX_CHUNK_SIZE;
        if (file_pos != offset) {
            if (fseek(f, (long)offset, SEEK_SET) != 0) {
                ESP_LOGW(TAG, "%s: fseek auf %" PRIu64 " fehlgeschlagen", cand->rel, offset);
                break;
            }
            file_pos = offset;
        }
        size_t got = fread(chunk, 1, n, f);
        if (got != n) {
            ESP_LOGW(TAG, "%s: Lesefehler bei %" PRIu64 " (%u von %u Byte)", cand->rel, offset, (unsigned)got, (unsigned)n);
            result = online() ? UP_FAILED : UP_ABORT;
            break;
        }
        file_pos += got;

        snprintf(url, sizeof(url), "%s/api/v1/upload?path=%s&size=%" PRIu64 "&offset=%" PRIu64,
                 base, enc, cand->size, offset);
        err = http_retry(ctx, HTTP_METHOD_PUT, url, (const char *)chunk, (int)n, SMALL_RESP_MAX, &status);
        if (err == ESP_ERR_INVALID_STATE) {
            result = UP_ABORT;
            break;
        }
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "%s: Upload bei %" PRIu64 " gescheitert (%s), beim nächsten Sync weiter",
                     cand->rel, offset, esp_err_to_name(err));
            break;
        }
        if (status == 200) {
            uint64_t r = 0;
            if (!parse_progress(ctx->buf, ctx->len, &r, &complete)) {
                ESP_LOGW(TAG, "%s: unerwartete Antwort", cand->rel);
                break;
            }
            if (!complete && r <= offset) {
                ESP_LOGW(TAG, "%s: kein Fortschritt (Server %" PRIu64 ")", cand->rel, r);
                break;
            }
            res->bytes += n;
            offset = r;
        } else if (status == 409) {
            uint64_t r = 0;
            bool c = false;
            if (++conflicts > MAX_CONFLICTS || !parse_progress(ctx->buf, ctx->len, &r, &c) || r > cand->size) {
                ESP_LOGW(TAG, "%s: Offset-Konflikt nicht lösbar", cand->rel);
                break;
            }
            ESP_LOGI(TAG, "%s: Server hat %" PRIu64 " Byte, mache dort weiter", cand->rel, r);
            offset = r;
            complete = c;
        } else if (status == 401 || status == 403) {
            snprintf(msg, msg_cap, "Token abgelehnt (HTTP %d)", status);
            result = UP_ABORT;
            break;
        } else {
            ESP_LOGW(TAG, "%s: HTTP %d: %.120s", cand->rel, status, ctx->buf ? ctx->buf : "");
            break;
        }
    }
    fclose(f);
    if (complete) {
        int64_t dt = esp_timer_get_time() - t0;
        ESP_LOGI(TAG, "%s: fertig (%" PRIu64 " Byte, %.1f s)", cand->rel, cand->size, dt / 1e6);
        res->uploaded++;
        result = UP_DONE;
    }
    return result;
}

/* ------------------------------------------------------------ Ergebnis */

static void result_save(const sync_result_t *r)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_blob(h, NVS_KEY_LAST, r, sizeof(*r));
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Ergebnis nicht gespeichert: %s", esp_err_to_name(err));
    }
}

bool sync_result_load(sync_result_t *out)
{
    nvs_handle_t h;
    memset(out, 0, sizeof(*out));
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t n = sizeof(*out);
    esp_err_t err = nvs_get_blob(h, NVS_KEY_LAST, out, &n);
    nvs_close(h);
    if (err != ESP_OK || n != sizeof(*out) || out->version != SYNC_RESULT_VERSION) {
        memset(out, 0, sizeof(*out));
        return false;
    }
    out->msg[sizeof(out->msg) - 1] = '\0';
    return true;
}

void sync_result_format(const sync_result_t *r, char *buf, size_t cap)
{
    snprintf(buf, cap,
             "%s: Start %" PRIu32 ", nach %" PRIu32 " min %02" PRIu32 " s Laufzeit; %u Dateien geprüft, "
             "%u neu, %u übersprungen, %u Fehler, %.1f KB in %.1f s%s%s",
             r->ok ? "OK" : "MIT FEHLERN", r->boot, r->uptime_s / 60, r->uptime_s % 60,
             r->found, r->uploaded, r->skipped, r->errors, r->bytes / 1024.0, r->duration_ms / 1000.0,
             r->msg[0] ? " – " : "", r->msg);
}

void sync_init(void)
{
    nvs_handle_t h;
    uint32_t boots = 0;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_get_u32(h, NVS_KEY_BOOTS, &boots);
        boots++;
        nvs_set_u32(h, NVS_KEY_BOOTS, boots);
        nvs_commit(h);
        nvs_close(h);
    }
    s_boot = boots;
}

/* ------------------------------------------------------------ Ablauf */

bool sync_run(const app_config_t *cfg, sync_result_t *out)
{
    int64_t t0 = esp_timer_get_time();
    sync_result_t res = { .version = SYNC_RESULT_VERSION, .boot = s_boot };
    avx_manifest_t manifest;
    avx_manifest_init(&manifest);
    cand_list_t cands = { 0 };
    uint8_t *chunk = NULL;
    bool mounted = false;
    http_ctx_t ctx = { 0 };

    ESP_LOGI(TAG, "Sync startet (Server %s)", cfg->url);
    char auth[CFG_TOKEN_MAX + 16];
    snprintf(auth, sizeof(auth), "Bearer %s", cfg->token);
    char first_url[URL_MAX];
    snprintf(first_url, sizeof(first_url), "%s/api/v1/manifest", cfg->url);
    esp_http_client_config_t hc = {
        .url = first_url,
        .timeout_ms = HTTP_TIMEOUT_MS,
        .event_handler = http_event,
        .user_data = &ctx,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .keep_alive_enable = true,
        .disable_auto_redirect = true,
    };
    ctx.client = esp_http_client_init(&hc);
    if (!ctx.client) {
        snprintf(res.msg, sizeof(res.msg), "HTTP-Client: kein Speicher");
        goto done;
    }
    esp_http_client_set_header(ctx.client, "Authorization", auth);
    esp_http_client_set_header(ctx.client, "X-Avinox-Device", report_device_id());
    memset(auth, 0, sizeof(auth));

    if (fetch_manifest(&ctx, cfg->url, &manifest, res.msg, sizeof(res.msg)) != ESP_OK) {
        goto done;
    }

    if (usb_drive_mount() != ESP_OK) {
        snprintf(res.msg, sizeof(res.msg), "Laufwerk nicht einhängbar");
        goto done;
    }
    mounted = true;
    scan_drive(&cands);
    res.found = cands.count;

    chunk = malloc(CONFIG_AVX_CHUNK_SIZE);
    if (!chunk) {
        snprintf(res.msg, sizeof(res.msg), "Kein Speicher für Upload-Puffer");
        goto done;
    }
    for (size_t i = 0; i < cands.count; i++) {
        const candidate_t *c = &cands.items[i];
        if (avx_manifest_contains(&manifest, c->rel, c->size)) {
            res.skipped++;
            continue;
        }
        if (c->size == 0) {
            ESP_LOGI(TAG, "%s: leer, übersprungen", c->rel);
            res.skipped++;
            continue;
        }
        up_result_t r = upload_file(&ctx, cfg->url, c, chunk, &res, res.msg, sizeof(res.msg));
        if (r == UP_FAILED) {
            res.errors++;
            if (!res.msg[0]) {
                snprintf(res.msg, sizeof(res.msg), "%.90s: Upload fehlgeschlagen", c->rel);
            }
        } else if (r == UP_ABORT) {
            res.errors++;
            if (!res.msg[0]) {
                snprintf(res.msg, sizeof(res.msg), "abgebrochen (%s weg)", wifi_sta_connected() ? "Laufwerk" : "WLAN");
            }
            break;
        }
    }
    res.ok = res.errors == 0 && !res.msg[0];
    if (res.ok && cands.count == 0) {
        /* leeres Laufwerk ist kein Fehler, aber einen Hinweis wert */
        snprintf(res.msg, sizeof(res.msg), "keine passenden Dateien auf dem Laufwerk");
    }

done:
    if (mounted) {
        usb_drive_unmount();
    }
    free(chunk);
    free(cands.items);
    avx_manifest_free(&manifest);
    if (ctx.client) {
        esp_http_client_cleanup(ctx.client);
    }
    free(ctx.buf);

    int64_t now = esp_timer_get_time();
    res.duration_ms = (uint32_t)((now - t0) / 1000);
    res.uptime_s = (uint32_t)(now / 1000000);
    char line[256];
    sync_result_format(&res, line, sizeof(line));
    if (res.ok) {
        ESP_LOGI(TAG, "Sync %s", line);
    } else {
        ESP_LOGW(TAG, "Sync %s", line);
    }
    result_save(&res);
    report_send(cfg, &res);
    if (out) {
        *out = res;
    }
    return res.ok;
}
