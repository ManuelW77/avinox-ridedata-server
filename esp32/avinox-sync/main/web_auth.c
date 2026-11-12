#include "web_auth.h"

#include <string.h>
#include <strings.h>
#include <stdint.h>
#include <stdio.h>
#include "mbedtls/base64.h"

#define DECODED_MAX 192
#define EXPECTED_MAX 160

bool web_basic_auth_valid(const char *auth_hdr, const char *user, const char *pass)
{
    if (!auth_hdr || !user || !pass || strncasecmp(auth_hdr, "Basic ", 6) != 0) {
        return false;
    }
    const char *b64 = auth_hdr + 6;
    while (*b64 == ' ') {
        b64++;
    }

    unsigned char got[DECODED_MAX];
    unsigned char exp[EXPECTED_MAX];
    memset(got, 0, sizeof(got));
    memset(exp, 0, sizeof(exp));

    size_t olen = 0;
    bool ok = false;
    size_t elen = strlen(user) + 1 + strlen(pass);
    if (elen < sizeof(exp) &&
        mbedtls_base64_decode(got, sizeof(got), &olen, (const unsigned char *)b64, strlen(b64)) == 0) {
        snprintf((char *)exp, sizeof(exp), "%s:%s", user, pass);
        unsigned diff = (unsigned)(olen ^ elen);
        for (size_t i = 0; i < elen; i++) {
            diff |= got[i] ^ exp[i];        /* got ist komplett mit Nullen vorbelegt: kein Überlesen */
        }
        ok = diff == 0;
    }
    memset(got, 0, sizeof(got));
    memset(exp, 0, sizeof(exp));
    return ok;
}

bool web_origin_matches_host(const char *origin, const char *host)
{
    if (!origin || !host || !host[0]) {
        return false;
    }
    const char *h = strstr(origin, "://");
    if (!h) {
        return false;                       /* z. B. "null" */
    }
    h += 3;
    size_t n = strcspn(h, "/");
    return n > 0 && strlen(host) == n && strncasecmp(h, host, n) == 0;
}
