#include "web_auth.h"

#include <string.h>
#include <strings.h>
#include <stdint.h>
#include <stdio.h>
#include "mbedtls/base64.h"

#define DECODED_MAX 192
#define EXPECTED_MAX 160

const char *web_auth_reason_str(web_auth_reason_t r)
{
    switch (r) {
    case AUTH_OK: return "ok";
    case AUTH_NO_HEADER: return "kein Authorization-Header";
    case AUTH_TRUNCATED: return "Header abgeschnitten/zu lang";
    case AUTH_NOT_BASIC: return "kein \"Basic\"";
    case AUTH_BAD_BASE64: return "Base64-Fehler";
    case AUTH_NO_COLON: return "kein Doppelpunkt";
    case AUTH_BAD_USER: return "Benutzer falsch";
    case AUTH_BAD_PASS: return "Passwort falsch";
    }
    return "?";
}

bool web_basic_auth_check(const char *auth_hdr, const char *user, const char *pass, web_auth_info_t *info)
{
    web_auth_info_t local;
    if (!info) {
        info = &local;
    }
    memset(info, 0, sizeof(*info));
    info->pass_len = -1;
    info->expected_pass_len = pass ? (int)strlen(pass) : 0;

    if (!user || !pass || !auth_hdr) {
        info->reason = AUTH_NO_HEADER;
        return false;
    }
    if (strncasecmp(auth_hdr, "Basic ", 6) != 0) {
        info->reason = AUTH_NOT_BASIC;
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
    if (mbedtls_base64_decode(got, sizeof(got) - 1, &olen, (const unsigned char *)b64, strlen(b64)) != 0) {
        info->reason = AUTH_BAD_BASE64;
    } else {
        const unsigned char *colon = memchr(got, ':', olen);
        if (!colon) {
            info->reason = AUTH_NO_COLON;
        } else {
            size_t ulen = (size_t)(colon - got);
            info->pass_len = (int)(olen - ulen - 1);
            size_t n = ulen < 32 ? ulen : 32;
            for (size_t i = 0; i < n; i++) {
                info->user[i] = (got[i] >= 0x20 && got[i] < 0x7F) ? (char)got[i] : '?';
            }
            info->user[n] = '\0';

            bool user_ok = ulen == strlen(user) && memcmp(got, user, ulen) == 0;
            bool pass_ok = false;
            if (elen < sizeof(exp)) {
                snprintf((char *)exp, sizeof(exp), "%s:%s", user, pass);
                unsigned diff = (unsigned)(olen ^ elen);
                for (size_t i = 0; i < elen; i++) {
                    diff |= got[i] ^ exp[i];    /* got ist komplett mit Nullen vorbelegt: kein Überlesen */
                }
                pass_ok = diff == 0;
            }
            ok = pass_ok;
            info->reason = ok ? AUTH_OK : (!user_ok ? AUTH_BAD_USER : AUTH_BAD_PASS);
        }
    }
    memset(got, 0, sizeof(got));
    memset(exp, 0, sizeof(exp));
    return ok;
}

bool web_basic_auth_valid(const char *auth_hdr, const char *user, const char *pass)
{
    return web_basic_auth_check(auth_hdr, user, pass, NULL);
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

bool web_referer_matches_host(const char *referer, const char *host)
{
    /* Gleiche Host-Prüfung; ein Pfad nach dem Host ist erlaubt. */
    return web_origin_matches_host(referer, host);
}
