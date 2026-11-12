#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "config.h"

/* Ergebnis eines Syncs, gespeichert im NVS (Namespace "sync", Blob "last"). */
typedef struct {
    uint32_t version;       /* SYNC_RESULT_VERSION */
    uint32_t boot;          /* Startzähler des ESP32 */
    uint32_t uptime_s;      /* Laufzeit seit Start beim Ende des Syncs */
    uint32_t duration_ms;
    uint16_t found;         /* passende Dateien auf dem Laufwerk */
    uint16_t uploaded;      /* neu: in diesem Sync vollständig hochgeladen */
    uint16_t skipped;       /* übersprungen: Server kennt Pfad + Größe schon */
    uint16_t errors;        /* nicht (vollständig) hochgeladen, nächster Sync setzt fort */
    uint64_t bytes;         /* in diesem Sync gesendete Nutzdaten */
    bool ok;                /* Manifest geholt, Laufwerk gelesen, keine Dateifehler */
    char msg[120];          /* wichtigste Meldung, z. B. "Manifest: HTTP 401" */
} sync_result_t;

#define SYNC_RESULT_VERSION 2

/* Startzähler erhöhen (einmal beim Start). */
void sync_init(void);
/* Ein Sync-Durchlauf: Manifest, Laufwerk durchsuchen, neue Dateien hochladen, Ergebnis speichern. */
bool sync_run(const app_config_t *cfg, sync_result_t *out);
/* Letztes gespeichertes Ergebnis; false, wenn keins vorhanden ist. */
bool sync_result_load(sync_result_t *out);
/* Ergebnis als einzeiliger deutscher Text. */
void sync_result_format(const sync_result_t *r, char *buf, size_t cap);
