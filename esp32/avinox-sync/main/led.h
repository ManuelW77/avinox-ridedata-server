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
    LED_NO_DRIVE,   /* orange blinkend: kein Laufwerk gefunden (Display aus / falsche USB-Buchse?) */
    LED_ERROR,      /* rot: schwerer Fehler (USB/Konfiguration) */
    LED_HOLD,       /* violett dauerhaft: BOOT >= 3 s, Loslassen = Einrichtungsmodus */
    LED_FACTORY,    /* rot schnell blinkend: BOOT >= 10 s, Werkseinstellung */
    LED_OTA,        /* weiß langsam blinkend: Firmware-Update wird geladen (als Override) */
} led_state_t;

void led_init(void);
void led_set_state(led_state_t state);
led_state_t led_get_state(void);

/* Vorrang-Überlagerung (z. B. BOOT-Taste): solange aktiv, zeigt die LED diesen Zustand;
 * led_set_state() merkt sich den normalen Zustand nur. Thread-sicher. */
void led_set_override(led_state_t state);
void led_clear_override(void);
