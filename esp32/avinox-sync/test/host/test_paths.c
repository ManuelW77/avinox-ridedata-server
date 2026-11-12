/*
 * Host-Test für Pfadfilter, Manifest-Menge und URL-Kodierung (ohne ESP-IDF).
 *   cd test/host && cc -Wall -Wextra -I../../main ../../main/sync_paths.c test_paths.c -o test_paths && ./test_paths
 */
#include <stdio.h>
#include <string.h>
#include "sync_paths.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FEHLER Zeile %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main(void)
{
    /* erlaubt (wie server PATTERNS) */
    CHECK(avx_path_allowed("ride_rec/ride_rec_1008.proto"));
    CHECK(avx_path_allowed("ride_rec/424242/ride_rec_1008.proto"));
    CHECK(avx_path_allowed("ride_rec/ride_rec_index.dat"));
    CHECK(avx_path_allowed("ride_rec/424242/ride_rec_index.dat"));
    CHECK(avx_path_allowed("ride_rec/a-b_C/ride_rec_1.proto"));
    CHECK(avx_path_allowed("ride_rec/ride_rec_1234567890.proto"));
    CHECK(avx_path_allowed("buried_rec/buried_file_cur.json"));
    CHECK(avx_path_allowed("hms_rec/hms_current_storage.proto"));
    /* nicht erlaubt */
    CHECK(!avx_path_allowed("ebike_log_1_20980101_000000.dat"));
    CHECK(!avx_path_allowed("ride_rec/ride_rec_12345678901.proto"));
    CHECK(!avx_path_allowed("ride_rec/ride_rec_.proto"));
    CHECK(!avx_path_allowed("ride_rec/ride_rec_12a.proto"));
    CHECK(!avx_path_allowed("ride_rec/ride_rec_12.PROTO"));
    CHECK(!avx_path_allowed("ride_rec/a/b/ride_rec_1.proto"));
    CHECK(!avx_path_allowed("ride_rec/a.b/ride_rec_1.proto"));
    CHECK(!avx_path_allowed("ride_rec//ride_rec_1.proto"));
    CHECK(!avx_path_allowed("ride_rec/.DS_Store"));
    CHECK(!avx_path_allowed("/ride_rec/ride_rec_1.proto"));
    CHECK(!avx_path_allowed("buried_rec/buried_file_old.json"));
    CHECK(!avx_path_allowed("System/ebike_log_index.dat"));
    char seg65[66];
    memset(seg65, 'a', 65);
    seg65[65] = '\0';
    CHECK(!avx_is_segment(seg65));
    seg65[64] = '\0';
    CHECK(avx_is_segment(seg65));
    CHECK(!avx_is_segment(""));

    /* Manifest */
    avx_manifest_t m;
    avx_manifest_init(&m);
    for (int i = 0; i < 100; i++) {
        char p[64];
        snprintf(p, sizeof(p), "ride_rec/424242/ride_rec_%d.proto", 1000 + i);
        CHECK(avx_manifest_add(&m, p, (uint64_t)i * 1000));
    }
    CHECK(avx_manifest_contains(&m, "ride_rec/424242/ride_rec_1008.proto", 8000));
    CHECK(!avx_manifest_contains(&m, "ride_rec/424242/ride_rec_1008.proto", 8001));   /* gewachsen = neu */
    CHECK(!avx_manifest_contains(&m, "ride_rec/ride_rec_1008.proto", 8000));
    avx_manifest_free(&m);
    CHECK(m.count == 0);

    /* URL-Kodierung */
    char out[64];
    CHECK(avx_url_encode("ride_rec/424242/ride_rec_1008.proto", out, sizeof(out)));
    CHECK(strcmp(out, "ride_rec%2F424242%2Fride_rec_1008.proto") == 0);
    CHECK(avx_url_encode("a b+ä", out, sizeof(out)));
    CHECK(strcmp(out, "a%20b%2B%C3%A4") == 0);
    CHECK(!avx_url_encode("abcdef", out, 4));

    printf(fails ? "%d Fehler\n" : "alle Tests ok\n", fails);
    return fails ? 1 : 0;
}
