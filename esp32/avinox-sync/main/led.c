#include "led.h"

#include <stdbool.h>
#include <stdint.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "led_strip.h"

static const char *TAG = "led";

static led_state_t s_state = LED_OFF;       /* normaler Zustand */
static led_state_t s_override = LED_OFF;
static bool s_override_active;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static led_strip_handle_t s_strip;
static TaskHandle_t s_task;

static void led_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_strip) {
        return;
    }
    led_strip_set_pixel(s_strip, 0, r, g, b);
    led_strip_refresh(s_strip);
}

static led_state_t led_effective(void)
{
    portENTER_CRITICAL(&s_lock);
    led_state_t s = s_override_active ? s_override : s_state;
    portEXIT_CRITICAL(&s_lock);
    return s;
}

static void led_wake(void)
{
    if (s_task) {
        xTaskNotifyGive(s_task);   /* nur der LED-Task spricht mit dem RMT-Treiber */
    }
}

static void led_task(void *arg)
{
    bool on = false;
    while (true) {
        on = !on;
        led_state_t cur = led_effective();
        switch (cur) {
        case LED_OFF:   led_rgb(0, 0, 0); break;
        case LED_START: led_rgb(24, 24, 24); break;
        case LED_SETUP: if (on) led_rgb(24, 0, 32); else led_rgb(0, 0, 0); break;
        case LED_WAIT:  if (on) led_rgb(0, 0, 32); else led_rgb(0, 0, 0); break;
        case LED_SYNC:  led_rgb(0, 24, 24); break;
        case LED_OK:    led_rgb(0, 32, 0); break;
        case LED_WARN:  led_rgb(32, 20, 0); break;
        case LED_NO_DRIVE: if (on) led_rgb(32, 10, 0); else led_rgb(0, 0, 0); break;
        case LED_ERROR: led_rgb(32, 0, 0); break;
        case LED_HOLD:  led_rgb(24, 0, 32); break;
        case LED_FACTORY: if (on) led_rgb(32, 0, 0); else led_rgb(0, 0, 0); break;
        case LED_OTA:   if (on) led_rgb(24, 24, 24); else led_rgb(0, 0, 0); break;
        }
        /* 500 ms Blinktakt; led_set_state() weckt früher auf */
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(cur == LED_FACTORY ? 120 : (cur == LED_NO_DRIVE || cur == LED_OTA) ? 1000 : 500));
    }
}

void led_init(void)
{
#if CONFIG_AVX_LED_GPIO >= 0
    led_strip_config_t strip_config = {
        .strip_gpio_num = CONFIG_AVX_LED_GPIO,
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
    if (xTaskCreate(led_task, "led", 2048, NULL, 1, &s_task) != pdPASS) {
        ESP_LOGW(TAG, "LED-Task nicht gestartet");
    }
#else
    ESP_LOGI(TAG, "Status-LED deaktiviert (AVX_LED_GPIO = -1)");
#endif
}

void led_set_state(led_state_t state)
{
    portENTER_CRITICAL(&s_lock);
    bool changed = s_state != state;
    s_state = state;
    bool visible = !s_override_active;
    portEXIT_CRITICAL(&s_lock);
    if (changed && visible) {
        led_wake();
    }
}

void led_set_override(led_state_t state)
{
    portENTER_CRITICAL(&s_lock);
    bool changed = !s_override_active || s_override != state;
    s_override = state;
    s_override_active = true;
    portEXIT_CRITICAL(&s_lock);
    if (changed) {
        led_wake();
    }
}

void led_clear_override(void)
{
    portENTER_CRITICAL(&s_lock);
    bool changed = s_override_active;
    s_override_active = false;
    portEXIT_CRITICAL(&s_lock);
    if (changed) {
        led_wake();
    }
}

led_state_t led_get_state(void)
{
    return led_effective();
}
