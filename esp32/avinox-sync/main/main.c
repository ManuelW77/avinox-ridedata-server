/*
 * Avinox-Sync: liest neue Fahrtdateien vom USB-Laufwerk des Avinox-Displays (nur lesend)
 * und lädt sie per WLAN auf den eigenen Server hoch. Ablauf und LED-Bedeutung: README.md.
 */
#include <stdio.h>
#include <inttypes.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "driver/gpio.h"

#include "config.h"
#include "led.h"
#include "portal.h"
#include "report.h"
#include "sync.h"
#include "usb_drive.h"
#include "wifi_sta.h"

static const char *TAG = "main";

#define BOOT_HOLD_MS        3000
#define BOOT_FACTORY_MS     10000
#define SYNC_INTERVAL_US    ((int64_t)CONFIG_AVX_SYNC_INTERVAL_MIN * 60 * 1000000)

static app_config_t s_cfg;

static void boot_gpio_init(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_AVX_BOOT_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&io);
}

static bool boot_pressed(void)
{
    return gpio_get_level(CONFIG_AVX_BOOT_GPIO) == 0;
}

/* BOOT im Betrieb (nach dem Einschalten): ab 3 s violett, Loslassen = Neustart in den Einrichtungsmodus;
 * ab 10 s rot schnell blinkend, dann Werkseinstellung (Konfiguration löschen) und Neustart. */
static void button_task(void *arg)
{
    int held_ms = 0;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(100));
        bool pressed = boot_pressed();
        int prev = held_ms;
        held_ms = pressed ? held_ms + 100 : 0;
        if (prev < BOOT_HOLD_MS && held_ms >= BOOT_HOLD_MS) {
            led_set_override(LED_HOLD);
        }
        if (prev < BOOT_FACTORY_MS && held_ms >= BOOT_FACTORY_MS) {
            led_set_override(LED_FACTORY);
            ESP_LOGW(TAG, "BOOT %d s gedrückt: Werkseinstellung, Konfiguration wird gelöscht", BOOT_FACTORY_MS / 1000);
            if (config_factory_reset() != ESP_OK) {
                ESP_LOGE(TAG, "Konfiguration nicht gelöscht");
            }
            while (boot_pressed()) {        /* erst nach Loslassen neu starten (GPIO 0 = Strapping-Pin) */
                vTaskDelay(pdMS_TO_TICKS(100));
            }
            vTaskDelay(pdMS_TO_TICKS(300));
            esp_restart();
        }
        if (prev > 0 && prev < BOOT_HOLD_MS && !pressed) {
            led_clear_override();           /* vor 3 s losgelassen: normale Anzeige */
        }
        if (prev >= BOOT_HOLD_MS && !pressed) {
            ESP_LOGW(TAG, "BOOT losgelassen: Neustart in den Einrichtungsmodus");
            led_set_override(LED_SETUP);
            if (config_request_portal() != ESP_OK) {
                ESP_LOGE(TAG, "Merker nicht gespeichert");
            }
            vTaskDelay(pdMS_TO_TICKS(300));
            esp_restart();
        }
    }
}

/* Beim Start schon gedrückt? Dann bis zu 3 s auf Loslassen warten. */
static bool boot_held_at_start(void)
{
    for (int t = 0; t < BOOT_HOLD_MS; t += 50) {
        if (!boot_pressed()) {
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    return true;
}

static void sync_task(void *arg)
{
    uint32_t seen_wifi = 0, seen_drive = 0;
    int64_t next_sync = 0;
    bool waiting_logged = false;
    int64_t no_drive_since = esp_timer_get_time();   /* Start bzw. letztes Abziehen */
    bool no_drive_logged = false;
    const int64_t no_drive_us = (int64_t)CONFIG_AVX_NO_DRIVE_HINT_S * 1000000;

    while (true) {
        if (usb_drive_host_failed()) {
            led_set_state(LED_ERROR);
            vTaskDelay(pdMS_TO_TICKS(5000));
            continue;
        }
        drive_state_t ds = usb_drive_state();
        bool wifi = wifi_sta_connected();
        bool no_drive_hint = false;
        if (ds != DRIVE_ABSENT) {
            no_drive_since = esp_timer_get_time();
            no_drive_logged = false;
        } else if (esp_timer_get_time() - no_drive_since >= no_drive_us) {
            led_state_t cur = led_get_state();
            no_drive_hint = cur != LED_HOLD && cur != LED_FACTORY;
            if (!no_drive_logged) {
                ESP_LOGW(TAG, "Kein Laufwerk gefunden - Display eingeschaltet? An der richtigen (nativen) USB-Buchse?");
                no_drive_logged = true;
            }
        }
        if (ds == DRIVE_FAILED) {
            led_set_state(LED_ERROR);       /* bis zum Abziehen */
        }
        if (ds != DRIVE_READY || !wifi) {
            if (no_drive_hint) {
                led_set_state(LED_NO_DRIVE);
            } else if (ds != DRIVE_FAILED) {
                led_set_state(LED_WAIT);
            }
            if (!waiting_logged) {
                ESP_LOGI(TAG, "Warte auf %s%s%s", ds != DRIVE_READY ? "Laufwerk" : "",
                         (ds != DRIVE_READY && !wifi) ? " und " : "", wifi ? "" : "WLAN");
                waiting_logged = true;
            }
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        waiting_logged = false;

        uint32_t gw = wifi_sta_generation(), gd = usb_drive_generation();
        int64_t now = esp_timer_get_time();
        bool fresh = gw != seen_wifi || gd != seen_drive;     /* neu angesteckt / neu verbunden */
        if (!fresh && now < next_sync) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        seen_wifi = gw;
        seen_drive = gd;

        led_set_state(LED_SYNC);
        sync_result_t res;
        bool ok = sync_run(&s_cfg, &res);
        led_set_state(ok ? LED_OK : LED_WARN);
        next_sync = esp_timer_get_time() + SYNC_INTERVAL_US;
        ESP_LOGI(TAG, "Nächster Sync in %d min (oder sofort nach neuem Anstecken/Verbinden)",
                 CONFIG_AVX_SYNC_INTERVAL_MIN);
    }
}

void app_main(void)
{
    if (config_nvs_init() != ESP_OK) {
        led_init();
        led_set_state(LED_ERROR);
        ESP_LOGE(TAG, "NVS nicht nutzbar, Abbruch");
        return;
    }
    led_init();
    led_set_state(LED_START);
    boot_gpio_init();
    sync_init();
    ESP_LOGI(TAG, "=== Avinox-Sync ===");
    vTaskDelay(pdMS_TO_TICKS(500));

    config_load(&s_cfg);
    bool want_portal = config_take_portal_request();
    if (boot_held_at_start()) {
        ESP_LOGI(TAG, "BOOT beim Start gedrückt");
        want_portal = true;
    }
    if (!config_complete(&s_cfg)) {
        ESP_LOGW(TAG, "Konfiguration unvollständig (SSID, Server-URL oder Token fehlt)");
        want_portal = true;
    }
    if (want_portal) {
        portal_run();       /* kehrt nicht zurück */
    }

    ESP_LOGI(TAG, "Server %s, WLAN '%s', Sync alle %d min", s_cfg.url, s_cfg.ssid, CONFIG_AVX_SYNC_INTERVAL_MIN);
    sync_result_t last;
    if (sync_result_load(&last)) {
        char line[256];
        sync_result_format(&last, line, sizeof(line));
        ESP_LOGI(TAG, "Letzter Sync: %s", line);
    }

    led_set_state(LED_WAIT);
    if (xTaskCreate(button_task, "button", 2560, NULL, 1, NULL) != pdPASS) {
        ESP_LOGW(TAG, "Tasten-Task nicht gestartet");
    }
    esp_err_t err = wifi_sta_start(&s_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "WLAN-Start fehlgeschlagen: %s (weiter, Sync nicht möglich)", esp_err_to_name(err));
    }
    report_start_heartbeat(&s_cfg);
    err = usb_drive_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "USB-Start fehlgeschlagen: %s", esp_err_to_name(err));
    }
    /* eigener Task mit großem Stack: TLS-Handshake, JSON und Pfadpuffer */
    if (xTaskCreate(sync_task, "sync", 12288, NULL, 3, NULL) != pdPASS) {
        ESP_LOGE(TAG, "Sync-Task nicht gestartet");
        led_set_state(LED_ERROR);
    }
}
