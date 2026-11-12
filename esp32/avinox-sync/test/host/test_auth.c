/*
 * Host-Test für Basic-Auth-Prüfung und Origin-Vergleich der Weboberfläche (ohne ESP-IDF, mit base64-Shim).
 *   cd test/host && cc -Wall -Wextra -Ishim -I../../main ../../main/web_auth.c test_auth.c -o test_auth && ./test_auth
 */
#include <stdio.h>
#include "web_auth.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FEHLER Zeile %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main(void)
{
    /* "admin:geheim123" */
    const char *ok = "Basic YWRtaW46Z2VoZWltMTIz";
    CHECK(web_basic_auth_valid(ok, "admin", "geheim123"));
    CHECK(web_basic_auth_valid("basic YWRtaW46Z2VoZWltMTIz", "admin", "geheim123"));
    CHECK(web_basic_auth_valid("Basic   YWRtaW46Z2VoZWltMTIz", "admin", "geheim123"));
    CHECK(!web_basic_auth_valid(ok, "admin", "geheim124"));
    CHECK(!web_basic_auth_valid(ok, "root", "geheim123"));
    CHECK(!web_basic_auth_valid(ok, "admin", "geheim12"));          /* kürzer erwartet */
    CHECK(!web_basic_auth_valid(ok, "admin", "geheim1234"));        /* länger erwartet */
    CHECK(!web_basic_auth_valid("Bearer YWRtaW46Z2VoZWltMTIz", "admin", "geheim123"));
    CHECK(!web_basic_auth_valid("Basic", "admin", "geheim123"));
    CHECK(!web_basic_auth_valid("Basic ", "admin", "geheim123"));
    CHECK(!web_basic_auth_valid("Basic !!!!", "admin", "geheim123"));
    CHECK(!web_basic_auth_valid("Basic YWRtaW46Z2VoZWltMTIz=", "admin", "geheim123"));
    CHECK(!web_basic_auth_valid(NULL, "admin", "geheim123"));
    /* Passwort mit Doppelpunkt: "admin:a:b:cdefgh" */
    CHECK(web_basic_auth_valid("Basic YWRtaW46YTpiOmNkZWZnaA==", "admin", "a:b:cdefgh"));
    /* viel zu langer Header */
    char big[600] = "Basic ";
    for (int i = 6; i < 599; i++) big[i] = 'A';
    big[599] = '\0';
    CHECK(!web_basic_auth_valid(big, "admin", "geheim123"));

    CHECK(web_origin_matches_host("http://192.168.1.5", "192.168.1.5"));
    CHECK(web_origin_matches_host("http://192.168.1.5:8080", "192.168.1.5:8080"));
    CHECK(web_origin_matches_host("http://Avinox.local", "avinox.local"));
    CHECK(web_origin_matches_host("http://192.168.1.5/", "192.168.1.5"));
    CHECK(!web_origin_matches_host("http://evil.example", "192.168.1.5"));
    CHECK(!web_origin_matches_host("http://192.168.1.5.evil.example", "192.168.1.5"));
    CHECK(!web_origin_matches_host("http://192.168.1.5", "192.168.1.5:8080"));
    CHECK(!web_origin_matches_host("null", "192.168.1.5"));
    CHECK(!web_origin_matches_host("http://", "192.168.1.5"));
    CHECK(!web_origin_matches_host("http://192.168.1.5", ""));

    if (fails) {
        printf("%d Fehler\n", fails);
        return 1;
    }
    printf("alle Tests ok\n");
    return 0;
}
