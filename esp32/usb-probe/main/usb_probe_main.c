/*
 * USB-Probe: Machbarkeitstest, ob ein ESP32-S3 als USB-Host das
 * Massenspeicher-Laufwerk des DJI-Avinox-Displays lesen kann.
 *
 * Es wird ausschließlich gelesen – keine Schreibzugriffe auf das Laufwerk.
 * Aufbau orientiert sich am ESP-IDF-Beispiel peripherals/usb/host/msc.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <dirent.h>
#include <sys/stat.h>
#include <inttypes.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_timer.h"
#include "esp_err.h"
#include "esp_log.h"
#include "usb/usb_host.h"
#include "usb/msc_host_vfs.h"
#include "led_strip.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "driver/gpio.h"

static const char *TAG = "usb-probe";

#define MNT_PATH        "/usb"
#define BLOCK_SIZE      4096
#define WAIT_HINT_MS    30000
#define MAX_DEPTH       6

/* ---------- Status-LED ---------- */

typedef enum { LED_WAIT, LED_DEVICE, LED_OK, LED_ERROR, LED_COUNTDOWN } led_state_t;
static volatile led_state_t s_led_state = LED_WAIT;
static led_strip_handle_t s_strip;

static void led_set(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_strip) {
        return;
    }
    led_strip_set_pixel(s_strip, 0, r, g, b);
    led_strip_refresh(s_strip);
}

static void led_task(void *arg)
{
    bool on = false;
    while (true) {
        switch (s_led_state) {
        case LED_WAIT:   on = !on; if (on) led_set(0, 0, 32); else led_set(0, 0, 0); break;
        case LED_DEVICE: led_set(32, 24, 0); break;
        case LED_OK:     led_set(0, 32, 0); break;
        case LED_ERROR:  led_set(32, 0, 0); break;
        case LED_COUNTDOWN: on = !on; if (on) led_set(24, 24, 24); else led_set(0, 0, 32); break;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

static void led_init(void)
{
#if CONFIG_PROBE_LED_GPIO >= 0
    led_strip_config_t strip_config = {
        .strip_gpio_num = CONFIG_PROBE_LED_GPIO,
        .max_leds = 1,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
    };
    esp_err_t err = led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "LED-Init fehlgeschlagen (%s), weiter ohne LED", esp_err_to_name(err));
        s_strip = NULL;
        return;
    }
    led_strip_clear(s_strip);
    xTaskCreate(led_task, "led", 2048, NULL, 1, NULL);
#else
    ESP_LOGI(TAG, "Status-LED deaktiviert (PROBE_LED_GPIO = -1)");
#endif
}

/* ---------- Bericht (RAM-Puffer + NVS) ---------- */

#define REPORT_MAX      (12 * 1024)
#define NVS_NS          "probe"
#define NVS_KEY_REPORT  "report"      /* alt, nur noch für die Migration */
#define NVS_KEY_REP_DEV   "rep_dev"   /* letzter Lauf mit Gerät */
#define NVS_KEY_RUN_DEV   "run_dev"
#define NVS_KEY_REP_WAIT  "rep_wait"  /* letzter Lauf ohne Gerät */
#define NVS_KEY_RUN_WAIT  "run_wait"
#define NVS_KEY_RUNS    "runs"

static char s_rep[REPORT_MAX];
static size_t s_rep_len;
static portMUX_TYPE s_rep_lock = portMUX_INITIALIZER_UNLOCKED;
static vprintf_like_t s_orig_vprintf;
static uint32_t s_run_no;
typedef struct {
    char *data;
    size_t len;
    uint32_t run;
} saved_report_t;

/* beim Start aus NVS geladen */
static saved_report_t s_rep_dev, s_rep_wait, s_rep_legacy;
static bool s_device_seen;      /* in diesem Lauf wurde ein USB-Gerät erkannt (EV_CONNECTED) */

static void rep_append(const char *line, size_t n)
{
    if (n > REPORT_MAX) {
        line += n - REPORT_MAX;
        n = REPORT_MAX;
    }
    portENTER_CRITICAL_SAFE(&s_rep_lock);
    if (s_rep_len + n > REPORT_MAX) {
        /* älteste Zeilen verwerfen: mindestens so viel, dass n passt, dann bis Zeilenende */
        size_t drop = s_rep_len + n - REPORT_MAX;
        while (drop < s_rep_len && s_rep[drop - 1] != '\n') {
            drop++;
        }
        if (drop > s_rep_len) {
            drop = s_rep_len;
        }
        memmove(s_rep, s_rep + drop, s_rep_len - drop);
        s_rep_len -= drop;
    }
    memcpy(s_rep + s_rep_len, line, n);
    s_rep_len += n;
    portEXIT_CRITICAL_SAFE(&s_rep_lock);
}

static int report_vprintf(const char *fmt, va_list ap)
{
    char line[256];
    va_list ap2;
    va_copy(ap2, ap);
    int n = vsnprintf(line, sizeof(line), fmt, ap2);
    va_end(ap2);
    if (n > 0) {
        rep_append(line, (size_t)n < sizeof(line) ? (size_t)n : sizeof(line) - 1);
    }
    return s_orig_vprintf ? s_orig_vprintf(fmt, ap) : n;
}

static void report_save(void)
{
    char *copy = malloc(REPORT_MAX);
    if (!copy) {
        return;
    }
    portENTER_CRITICAL(&s_rep_lock);
    size_t n = s_rep_len;
    memcpy(copy, s_rep, n);
    portEXIT_CRITICAL(&s_rep_lock);

    /* Lauf mit Gerät und Lauf ohne Gerät haben getrennte Plätze: ein Lauf ohne Gerät
     * überschreibt nie den Bericht vom Display. */
    const char *key_rep = s_device_seen ? NVS_KEY_REP_DEV : NVS_KEY_REP_WAIT;
    const char *key_run = s_device_seen ? NVS_KEY_RUN_DEV : NVS_KEY_RUN_WAIT;

    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_blob(h, key_rep, copy, n);
        if (err == ESP_OK) {
            err = nvs_set_u32(h, key_run, s_run_no);
        }
        if (err == ESP_OK) {
            err = nvs_set_u32(h, NVS_KEY_RUNS, s_run_no);
        }
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    free(copy);
    ESP_LOGI(TAG, "Bericht gespeichert (%s, Lauf %" PRIu32 ", %u Byte): %s",
             s_device_seen ? "mit Gerät" : "ohne Gerät", s_run_no, (unsigned)n, esp_err_to_name(err));
}

static void slot_load(nvs_handle_t h, const char *key_rep, const char *key_run, saved_report_t *r)
{
    uint32_t run = 0;
    nvs_get_u32(h, key_run, &run);
    r->run = run;
    size_t n = 0;
    if (nvs_get_blob(h, key_rep, NULL, &n) == ESP_OK && n > 0) {
        r->data = malloc(n);
        if (r->data && nvs_get_blob(h, key_rep, r->data, &n) == ESP_OK) {
            r->len = n;
        } else {
            free(r->data);
            r->data = NULL;
        }
    }
}

static void report_load(void)
{
    uint32_t runs = 0;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_get_u32(h, NVS_KEY_RUNS, &runs);
        slot_load(h, NVS_KEY_REP_DEV, NVS_KEY_RUN_DEV, &s_rep_dev);
        slot_load(h, NVS_KEY_REP_WAIT, NVS_KEY_RUN_WAIT, &s_rep_wait);
        /* Migration: alter Einzelbericht wird einmal ausgegeben (report_print_old) und dann gelöscht */
        size_t n = 0;
        if (nvs_get_blob(h, NVS_KEY_REPORT, NULL, &n) == ESP_OK && n > 0) {
            s_rep_legacy.data = malloc(n);
            if (s_rep_legacy.data && nvs_get_blob(h, NVS_KEY_REPORT, s_rep_legacy.data, &n) == ESP_OK) {
                s_rep_legacy.len = n;
                s_rep_legacy.run = runs;
            } else {
                free(s_rep_legacy.data);
                s_rep_legacy.data = NULL;
            }
        }
        nvs_close(h);
    }
    s_run_no = runs + 1;
}

static void print_blob(const saved_report_t *r)
{
    for (size_t off = 0; off < r->len; off += 128) {
        size_t c = r->len - off < 128 ? r->len - off : 128;
        fwrite(r->data + off, 1, c, stdout);
        fflush(stdout);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

static void report_print_old(void)
{
    if (s_rep_legacy.data && s_rep_legacy.len > 0) {
        printf("\n===== Alter Bericht (Lauf %" PRIu32 ") =====\n", s_rep_legacy.run);
        fflush(stdout);
        print_blob(&s_rep_legacy);
        printf("\n===== Ende des Berichts =====\n");
    }
    if (s_rep_dev.data && s_rep_dev.len > 0) {
        printf("\n===== Letzter Lauf MIT Gerät (Lauf %" PRIu32 ") =====\n", s_rep_dev.run);
        fflush(stdout);
        print_blob(&s_rep_dev);
        printf("\n===== Ende des Berichts =====\n");
    } else {
        printf("\nNoch kein Lauf mit Gerät gespeichert.\n");
    }
    if (s_rep_wait.data && s_rep_wait.len > 0) {
        printf("\n===== Letzter Lauf ohne Gerät (Lauf %" PRIu32 ") =====\n", s_rep_wait.run);
        fflush(stdout);
        print_blob(&s_rep_wait);
        printf("\n===== Ende des Berichts =====\n");
    } else {
        printf("\nNoch kein Lauf ohne Gerät gespeichert.\n");
    }
    fflush(stdout);
}

/* Alten Schlüssel `report` löschen, nachdem er ausgegeben wurde */
static void report_drop_legacy(void)
{
    if (!s_rep_legacy.data) {
        return;
    }
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_erase_key(h, NVS_KEY_REPORT);
        nvs_commit(h);
        nvs_close(h);
    }
    free(s_rep_legacy.data);
    s_rep_legacy.data = NULL;
    s_rep_legacy.len = 0;
}

static bool boot_pressed(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_PROBE_BOOT_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&io);
    return gpio_get_level(CONFIG_PROBE_BOOT_GPIO) == 0;
}

/* ---------- USB-Ereignisse ---------- */

typedef struct {
    enum { EV_CONNECTED, EV_DISCONNECTED } id;
    union {
        uint8_t address;
        msc_host_device_handle_t handle;
    } data;
} app_msg_t;

static QueueHandle_t s_queue;
static volatile bool s_usb_ok = false;

static void msc_event_cb(const msc_host_event_t *event, void *arg)
{
    app_msg_t msg;
    if (event->event == MSC_DEVICE_CONNECTED) {
        ESP_LOGI(TAG, "MSC-Gerät angesteckt (usb_addr=%d)", event->device.address);
        msg.id = EV_CONNECTED;
        msg.data.address = event->device.address;
    } else if (event->event == MSC_DEVICE_DISCONNECTED) {
        ESP_LOGI(TAG, "MSC-Gerät abgezogen");
        msg.id = EV_DISCONNECTED;
        msg.data.handle = event->device.handle;
    } else {
        ESP_LOGW(TAG, "Unbekanntes MSC-Ereignis %d", event->event);
        return;
    }
    xQueueSend(s_queue, &msg, portMAX_DELAY);
}

static void usb_task(void *arg)
{
    const usb_host_config_t host_config = { .intr_flags = ESP_INTR_FLAG_LOWMED };
    esp_err_t err = usb_host_install(&host_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb_host_install fehlgeschlagen: %s", esp_err_to_name(err));
        s_led_state = LED_ERROR;
        report_save();
        vTaskDelete(NULL);
        return;
    }

    const msc_host_driver_config_t msc_config = {
        .create_backround_task = true,
        .task_priority = 5,
        .stack_size = 4096,
        .callback = msc_event_cb,
    };
    err = msc_host_install(&msc_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "msc_host_install fehlgeschlagen: %s", esp_err_to_name(err));
        s_led_state = LED_ERROR;
        usb_host_uninstall();
        report_save();
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "USB-Host und MSC-Treiber installiert");
    s_usb_ok = true;

    while (true) {
        uint32_t flags;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
        if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) {
            usb_host_device_free_all();
        }
    }
}

/* ---------- Auswertung des Laufwerks ---------- */

static void wchar_to_ascii(const wchar_t *in, char *out, size_t len)
{
    size_t i = 0;
    for (; i + 1 < len && in[i]; i++) {
        out[i] = (in[i] >= 0x20 && in[i] < 0x7f) ? (char)in[i] : '?';
    }
    out[i] = '\0';
}

static bool has_prefix_suffix(const char *name, const char *pre, const char *suf)
{
    size_t n = strlen(name), p = strlen(pre), s = strlen(suf);
    return n >= p + s && strncasecmp(name, pre, p) == 0 && strcasecmp(name + n - s, suf) == 0;
}

typedef struct {
    int proto_count;
    int proto_ok;
    long biggest_size;
    char biggest_path[300];
} scan_result_t;

static void check_proto_magic(const char *path, scan_result_t *res)
{
    uint8_t magic[4] = {0};
    FILE *f = fopen(path, "rb");
    if (!f) {
        ESP_LOGE(TAG, "  %s: öffnen fehlgeschlagen", path);
        return;
    }
    size_t n = fread(magic, 1, sizeof(magic), f);
    fclose(f);
    bool ok = (n == 4 && magic[0] == 0xA5 && magic[1] == 0xA5 && magic[2] == 0xA5 && magic[3] == 0xA5);
    ESP_LOGI(TAG, "  Magic %s: %02X %02X %02X %02X -> %s", path,
             magic[0], magic[1], magic[2], magic[3], ok ? "OK" : "FEHLT");
    res->proto_count++;
    if (ok) {
        res->proto_ok++;
    }
}

static void list_recursive(const char *dir, int depth, scan_result_t *res)
{
    DIR *dh = opendir(dir);
    if (!dh) {
        ESP_LOGE(TAG, "opendir %s fehlgeschlagen", dir);
        return;
    }
    struct dirent *d;
    char path[300];
    while ((d = readdir(dh)) != NULL) {
        snprintf(path, sizeof(path), "%s/%s", dir, d->d_name);
        struct stat st;
        if (stat(path, &st) != 0) {
            ESP_LOGW(TAG, "  %s (stat fehlgeschlagen)", path);
            continue;
        }
        if (S_ISDIR(st.st_mode)) {
            ESP_LOGI(TAG, "  [DIR] %s", path);
            if (depth < MAX_DEPTH) {
                list_recursive(path, depth + 1, res);
            }
        } else {
            ESP_LOGI(TAG, "  %10ld  %s", (long)st.st_size, path);
            if (has_prefix_suffix(d->d_name, "ride_rec_", ".proto")) {
                check_proto_magic(path, res);
                if ((long)st.st_size > res->biggest_size) {
                    res->biggest_size = st.st_size;
                    strlcpy(res->biggest_path, path, sizeof(res->biggest_path));
                }
            }
        }
    }
    closedir(dh);
}

static bool speed_test(const char *path, long size)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        ESP_LOGE(TAG, "Speedtest: %s nicht lesbar", path);
        return false;
    }
    setvbuf(f, NULL, _IOFBF, BLOCK_SIZE);
    uint8_t *buf = malloc(BLOCK_SIZE);
    if (!buf) {
        fclose(f);
        ESP_LOGE(TAG, "Speedtest: kein Speicher");
        return false;
    }
    int64_t t0 = esp_timer_get_time();
    size_t total = 0, n;
    while ((n = fread(buf, 1, BLOCK_SIZE, f)) > 0) {
        total += n;
    }
    int64_t dt = esp_timer_get_time() - t0;
    bool err = ferror(f);
    fclose(f);
    free(buf);
    if (err || (long)total != size) {
        ESP_LOGE(TAG, "Speedtest: Lesefehler nach %u von %ld Byte", (unsigned)total, size);
        return false;
    }
    double kbs = dt > 0 ? (total / 1024.0) / (dt / 1e6) : 0;
    ESP_LOGI(TAG, "Speedtest: %u Byte in %.2f s = %.1f KB/s (%s)",
             (unsigned)total, dt / 1e6, kbs, path);
    return true;
}

static bool probe_drive(void)
{
    bool ok = true;

    ESP_LOGI(TAG, "--- Wurzelverzeichnis %s ---", MNT_PATH);
    DIR *dh = opendir(MNT_PATH);
    if (!dh) {
        ESP_LOGE(TAG, "Wurzelverzeichnis nicht lesbar");
        return false;
    }
    int ebike_logs = 0;
    struct dirent *d;
    while ((d = readdir(dh)) != NULL) {
        ESP_LOGI(TAG, "  %s%s", d->d_name, d->d_type == DT_DIR ? "/" : "");
        if (has_prefix_suffix(d->d_name, "ebike_log_", ".dat")) {
            ebike_logs++;   /* nur zählen, nicht öffnen */
        }
    }
    closedir(dh);

    ESP_LOGI(TAG, "--- ride_rec/ rekursiv ---");
    scan_result_t res = { 0 };
    res.biggest_size = -1;
    struct stat st;
    if (stat(MNT_PATH "/ride_rec", &st) == 0 && S_ISDIR(st.st_mode)) {
        list_recursive(MNT_PATH "/ride_rec", 0, &res);
    } else {
        ESP_LOGW(TAG, "ride_rec/ nicht gefunden");
        ok = false;
    }
    ESP_LOGI(TAG, "ride_rec_*.proto: %d Dateien, %d mit Magic A5A5A5A5", res.proto_count, res.proto_ok);

    if (stat(MNT_PATH "/buried_rec/buried_file_cur.json", &st) == 0) {
        ESP_LOGI(TAG, "buried_rec/buried_file_cur.json: %ld Byte", (long)st.st_size);
    } else {
        ESP_LOGI(TAG, "buried_rec/buried_file_cur.json nicht vorhanden");
    }

    ESP_LOGI(TAG, "ebike_log_*.dat im Wurzelordner: %d", ebike_logs);

    if (res.biggest_size >= 0) {
        ESP_LOGI(TAG, "Speedtest mit größter Datei (%ld Byte)", res.biggest_size);
        if (!speed_test(res.biggest_path, res.biggest_size)) {
            ok = false;
        }
    } else {
        ESP_LOGW(TAG, "Keine ride_rec_*.proto für Speedtest");
    }
    return ok;
}

static void handle_device(uint8_t addr, msc_host_device_handle_t *out_dev)
{
    msc_host_device_handle_t dev = NULL;
    msc_host_vfs_handle_t vfs = NULL;
    *out_dev = NULL;

    esp_err_t err = msc_host_install_device(addr, &dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Enumeration/msc_host_install_device fehlgeschlagen: %s", esp_err_to_name(err));
        s_led_state = LED_ERROR;
        report_save();
        return;
    }
    *out_dev = dev;

    msc_host_device_info_t info;
    err = msc_host_get_device_info(dev, &info);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "msc_host_get_device_info fehlgeschlagen: %s", esp_err_to_name(err));
        s_led_state = LED_ERROR;
        report_save();
        return;
    }
    char manu[64], prod[64];
    wchar_to_ascii(info.iManufacturer, manu, sizeof(manu));
    wchar_to_ascii(info.iProduct, prod, sizeof(prod));
    /* Seriennummer bewusst nicht loggen */
    ESP_LOGI(TAG, "VID=0x%04X PID=0x%04X Hersteller='%s' Produkt='%s'",
             info.idVendor, info.idProduct, manu, prod);
    ESP_LOGI(TAG, "Sektorgröße=%" PRIu32 " Sektoren=%" PRIu32 " (%llu MB)",
             info.sector_size, info.sector_count,
             ((uint64_t)info.sector_size * info.sector_count) / (1024 * 1024));

    const esp_vfs_fat_mount_config_t mount_config = {
        .format_if_mount_failed = false,   /* niemals formatieren */
        .max_files = 3,
        .allocation_unit_size = 0,
    };
    err = msc_host_vfs_register(dev, MNT_PATH, &mount_config, &vfs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Mount unter %s fehlgeschlagen: %s", MNT_PATH, esp_err_to_name(err));
        s_led_state = LED_ERROR;
        report_save();
        return;
    }
    ESP_LOGI(TAG, "Laufwerk unter %s eingehängt (nur lesend benutzt)", MNT_PATH);

    bool ok = probe_drive();

    err = msc_host_vfs_unregister(vfs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Aushängen fehlgeschlagen: %s", esp_err_to_name(err));
        ok = false;
    } else {
        ESP_LOGI(TAG, "Laufwerk ausgehängt");
    }
    s_led_state = ok ? LED_OK : LED_ERROR;
    ESP_LOGI(TAG, "Ergebnis: %s. Bitte Display abziehen.", ok ? "LAUFWERK GELESEN" : "FEHLER");
    report_save();
}

void app_main(void)
{
    esp_err_t nerr = nvs_flash_init();
    if (nerr == ESP_ERR_NVS_NO_FREE_PAGES || nerr == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    report_load();
    led_init();

    ESP_LOGI(TAG, "=== USB-Probe (Lauf %" PRIu32 ") ===", s_run_no);
    report_print_old();
    report_drop_legacy();
    s_orig_vprintf = esp_log_set_vprintf(report_vprintf);
    ESP_LOGI(TAG, "=== USB-Probe (Lauf %" PRIu32 ") ===", s_run_no);

    s_led_state = LED_COUNTDOWN;
    bool log_mode = false;
    for (int i = 10; i > 0 && !log_mode; i--) {
        ESP_LOGI(TAG, "Host-Modus startet in %d s - BOOT gedrückt halten, um im Log-Modus zu bleiben", i);
        for (int t = 0; t < 10; t++) {
            if (boot_pressed()) {
                log_mode = true;
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
    if (log_mode) {
        s_led_state = LED_WAIT;
        esp_log_set_vprintf(s_orig_vprintf);   /* Log-Modus: Bericht nicht überschreiben */
        while (true) {
            printf("\nLog-Modus (BOOT gedrückt, USB-Host nicht gestartet).\n");
            report_print_old();
            vTaskDelay(pdMS_TO_TICKS(30000));
        }
    }
    s_led_state = LED_WAIT;

    s_queue = xQueueCreate(5, sizeof(app_msg_t));
    if (!s_queue || xTaskCreate(usb_task, "usb", 4096, NULL, 2, NULL) != pdPASS) {
        ESP_LOGE(TAG, "Initialisierung fehlgeschlagen");
        s_led_state = LED_ERROR;
        report_save();
        return;
    }

    msc_host_device_handle_t current = NULL;
    bool wait_saved = false;
    int64_t wait_start = esp_timer_get_time();
    ESP_LOGI(TAG, "Warte auf USB-Massenspeicher ...");

    while (true) {
        app_msg_t msg;
        if (xQueueReceive(s_queue, &msg, pdMS_TO_TICKS(1000)) != pdTRUE) {
            if (current == NULL && s_led_state == LED_WAIT &&
                    esp_timer_get_time() - wait_start > (int64_t)WAIT_HINT_MS * 1000) {
                ESP_LOGW(TAG, "Seit 30 s kein Gerät%s. VBUS (5 V am Display?), OTG-Adapter und Kabel prüfen.",
                         s_usb_ok ? "" : " (USB-Host nicht installiert!)");
                wait_start = esp_timer_get_time();
                if (!wait_saved) {      /* nur einmal pro Lauf, schont den Flash */
                    wait_saved = true;
                    report_save();
                }
            }
            continue;
        }
        if (msg.id == EV_CONNECTED) {
            s_device_seen = true;
            if (current != NULL) {
                ESP_LOGW(TAG, "Weiteres Gerät ignoriert");
                continue;
            }
            s_led_state = LED_DEVICE;
            handle_device(msg.data.address, &current);
        } else if (msg.id == EV_DISCONNECTED) {
            if (current != NULL && current == msg.data.handle) {
                esp_err_t err = msc_host_uninstall_device(current);
                if (err != ESP_OK) {
                    ESP_LOGE(TAG, "msc_host_uninstall_device: %s", esp_err_to_name(err));
                }
                current = NULL;
            }
            s_led_state = LED_WAIT;
            wait_start = esp_timer_get_time();
            ESP_LOGI(TAG, "Warte auf USB-Massenspeicher ...");
        }
    }
}
