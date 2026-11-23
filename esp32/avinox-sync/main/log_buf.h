#pragma once

#include <stddef.h>
#include <stdint.h>

/*
 * Ringpuffer für alle ESP_LOG-Ausgaben (zusätzlich zur seriellen Schnittstelle), damit das Log ohne Kabel
 * im Browser lesbar ist (GET /log). Liegt in .noinit und übersteht deshalb Software-Neustart und Absturz;
 * bei Kaltstart (Power-on) wird er geleert. Keine Seriennummer, kein Token, kein WLAN-Passwort loggen!
 */

/* So früh wie möglich in app_main aufrufen: prüft/leert den Puffer, hängt sich in esp_log ein und schreibt
 * eine Zeile mit Reset-Grund und Firmware-Version. */
void log_buf_init(void);

/* Aktuelle Endposition (für log_buf_read: nur bis hierhin lesen). */
uint32_t log_buf_end(void);

/* Liest ab der absoluten Position *pos bis höchstens end, maximal cap Byte nach out, und rückt *pos weiter.
 * Ist der Anfang inzwischen überschrieben (oder der Puffer geleert), springt *pos zum ältesten Rest.
 * Rückgabe 0 = nichts mehr zu lesen. Mit *pos = 0 beginnen. */
size_t log_buf_read(uint32_t *pos, uint32_t end, char *out, size_t cap);

/* Puffer leeren. */
void log_buf_clear(void);
