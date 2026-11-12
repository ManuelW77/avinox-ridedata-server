#include "sync_paths.h"

#include <stdlib.h>
#include <string.h>

static bool is_seg_char(char c)
{
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
}

static bool seg_n(const char *s, size_t n)
{
    if (n < 1 || n > 64) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (!is_seg_char(s[i])) {
            return false;
        }
    }
    return true;
}

bool avx_is_segment(const char *name)
{
    return seg_n(name, strlen(name));
}

bool avx_is_ride_name(const char *name)
{
    static const char pre[] = "ride_rec_";
    static const char suf[] = ".proto";
    size_t n = strlen(name), p = sizeof(pre) - 1, s = sizeof(suf) - 1;
    if (n <= p + s || strncmp(name, pre, p) != 0 || strcmp(name + n - s, suf) != 0) {
        return false;
    }
    size_t digits = n - p - s;
    if (digits < 1 || digits > 10) {
        return false;
    }
    for (size_t i = p; i < p + digits; i++) {
        if (name[i] < '0' || name[i] > '9') {
            return false;
        }
    }
    return true;
}

bool avx_is_index_name(const char *name)
{
    return strcmp(name, "ride_rec_index.dat") == 0;
}

bool avx_path_allowed(const char *rel)
{
    if (strcmp(rel, "buried_rec/buried_file_cur.json") == 0 ||
            strcmp(rel, "hms_rec/hms_current_storage.proto") == 0) {
        return true;
    }
    static const char root[] = "ride_rec/";
    if (strncmp(rel, root, sizeof(root) - 1) != 0) {
        return false;
    }
    const char *rest = rel + sizeof(root) - 1;
    const char *slash = strchr(rest, '/');
    const char *file = rest;
    if (slash) {
        if (!seg_n(rest, (size_t)(slash - rest))) {
            return false;
        }
        file = slash + 1;
        if (strchr(file, '/')) {
            return false;   /* nur eine Ordnerebene */
        }
    }
    return avx_is_ride_name(file) || avx_is_index_name(file);
}

bool avx_url_encode(const char *in, char *out, size_t cap)
{
    static const char hex[] = "0123456789ABCDEF";
    size_t o = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {
        unsigned char c = *p;
        bool plain = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
                     c == '-' || c == '_' || c == '.' || c == '~';
        size_t need = plain ? 1 : 3;
        if (o + need >= cap) {
            if (cap) {
                out[0] = '\0';
            }
            return false;
        }
        if (plain) {
            out[o++] = (char)c;
        } else {
            out[o++] = '%';
            out[o++] = hex[c >> 4];
            out[o++] = hex[c & 0x0F];
        }
    }
    if (cap == 0) {
        return false;
    }
    out[o] = '\0';
    return true;
}

void avx_manifest_init(avx_manifest_t *m)
{
    m->items = NULL;
    m->count = 0;
    m->cap = 0;
}

bool avx_manifest_add(avx_manifest_t *m, const char *path, uint64_t size)
{
    if (m->count == m->cap) {
        size_t ncap = m->cap ? m->cap * 2 : 32;
        avx_manifest_entry_t *n = realloc(m->items, ncap * sizeof(*n));
        if (!n) {
            return false;
        }
        m->items = n;
        m->cap = ncap;
    }
    char *copy = strdup(path);
    if (!copy) {
        return false;
    }
    m->items[m->count].path = copy;
    m->items[m->count].size = size;
    m->count++;
    return true;
}

bool avx_manifest_contains(const avx_manifest_t *m, const char *path, uint64_t size)
{
    for (size_t i = 0; i < m->count; i++) {
        if (m->items[i].size == size && strcmp(m->items[i].path, path) == 0) {
            return true;
        }
    }
    return false;
}

void avx_manifest_free(avx_manifest_t *m)
{
    for (size_t i = 0; i < m->count; i++) {
        free(m->items[i].path);
    }
    free(m->items);
    avx_manifest_init(m);
}
