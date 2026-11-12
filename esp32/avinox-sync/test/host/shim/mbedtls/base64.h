/* Minimaler Ersatz für mbedtls/base64.h, nur für den Host-Test (die Firmware nutzt das echte mbedtls). */
#pragma once
#include <stddef.h>
#include <string.h>

static inline int mbedtls_base64_decode(unsigned char *dst, size_t dlen, size_t *olen,
                                        const unsigned char *src, size_t slen)
{
    static const char tab[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t o = 0;
    unsigned acc = 0;
    int bits = 0;
    size_t pad = 0;
    if (slen % 4) {
        return -0x002C;                 /* MBEDTLS_ERR_BASE64_INVALID_CHARACTER */
    }
    for (size_t i = 0; i < slen; i++) {
        if (src[i] == '=') {
            pad++;
            continue;
        }
        const char *p = pad ? NULL : strchr(tab, src[i]);
        if (!p || !src[i]) {
            return -0x002C;
        }
        acc = (acc << 6) | (unsigned)(p - tab);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            if (o >= dlen) {
                return -0x002A;         /* BUFFER_TOO_SMALL */
            }
            dst[o++] = (unsigned char)(acc >> bits);
        }
    }
    *olen = o;
    return 0;
}
