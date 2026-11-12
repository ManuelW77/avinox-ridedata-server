#pragma once

/*
 * Reine Hilfsfunktionen ohne ESP-IDF-Abhängigkeit (auf dem Host testbar, siehe test/host/):
 * Pfadfilter wie auf dem Server (server/src/avinox/server/store.py, PATTERNS),
 * Manifest-Menge (Pfad + Größe) und URL-Kodierung.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Ordnername unter ride_rec/: [A-Za-z0-9_-]{1,64} */
bool avx_is_segment(const char *name);
/* ride_rec_<1..10 Ziffern>.proto */
bool avx_is_ride_name(const char *name);
/* ride_rec_index.dat */
bool avx_is_index_name(const char *name);
/* Pfad relativ zur Laufwerkswurzel, mit '/' – entspricht einem der Server-Muster? */
bool avx_path_allowed(const char *rel);

/* Prozentkodierung für Query-Parameter (alles außer A-Z a-z 0-9 - _ . ~).
 * Gibt false zurück, wenn out zu klein ist. */
bool avx_url_encode(const char *in, char *out, size_t cap);

/* Bekannte Dateien vom Server (Pfad + Größe). */
typedef struct {
    char *path;
    uint64_t size;
} avx_manifest_entry_t;

typedef struct {
    avx_manifest_entry_t *items;
    size_t count;
    size_t cap;
} avx_manifest_t;

void avx_manifest_init(avx_manifest_t *m);
bool avx_manifest_add(avx_manifest_t *m, const char *path, uint64_t size);
bool avx_manifest_contains(const avx_manifest_t *m, const char *path, uint64_t size);
void avx_manifest_free(avx_manifest_t *m);
