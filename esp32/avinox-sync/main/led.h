#pragma once

/* Status-LED (WS2812). Bedeutung der Zustände siehe README. */
typedef enum {
    LED_OFF = 0,
    LED_START,      /* weiß, kurz beim Start */
    LED_SETUP,      /* violett blinkend: Einrichtungsmodus */
    LED_WAIT,       /* blau blinkend: wartet auf Laufwerk und/oder WLAN */
    LED_SYNC,       /* cyan: Sync läuft */
    LED_OK,         /* grün: Sync ok, alles aktuell */
    LED_WARN,       /* gelb: Sync mit Fehlern */
    LED_ERROR,      /* rot: schwerer Fehler (USB/Konfiguration) */
    LED_HOLD,       /* violett dauerhaft: BOOT >= 3 s, Loslassen = Einrichtungsmodus */
    LED_FACTORY,    /* rot schnell blinkend: BOOT >= 10 s, Werkseinstellung */
} led_state_t;

void led_init(void);
void led_set_state(led_state_t state);
led_state_t led_get_state(void);
