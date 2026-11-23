#include "log_buf.h"

#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_debug_helpers.h"
#include "esp_private/panic_internal.h"
#include "xtensa_context.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define LOG_SIZE        (16 * 1024)
#define LINE_MAX_LEN    200
#define MAGIC           0x41564C47u     /* "AVLG" */

typedef struct {
    uint32_t magic;
    uint32_t total;         /* seit dem Leeren geschriebene Byte (absolut); Schreibposition = total % LOG_SIZE */
    uint32_t check;         /* magic ^ total ^ Konstante */
    char data[LOG_SIZE];
} log_store_t;

/* .noinit: wird beim Neustart nicht angefasst (internes RAM, PSRAM ist nicht immer da) */
static __NOINIT_ATTR log_store_t s_log;
static atomic_flag s_busy = ATOMIC_FLAG_INIT;
static vprintf_like_t s_old_vprintf;

static uint32_t calc_check(void)
{
    return s_log.magic ^ s_log.total ^ 0x5A5AA5A5u;
}

static void reset_store(void)
{
    s_log.magic = MAGIC;
    s_log.total = 0;
    s_log.check = calc_check();
}

/* Ohne Sperre aufrufen nur, wenn s_busy gehalten wird. */
static void put(const char *s, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        s_log.data[s_log.total % LOG_SIZE] = s[i];
        s_log.total++;
    }
    s_log.check = calc_check();
}

/* Kopiert text ohne ANSI-Farbfolgen (ESC [ ... Buchstabe) nach out, liefert die Länge. */
static size_t strip_ansi(const char *in, size_t n, char *out)
{
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        if (in[i] == '\033') {
            if (i + 1 < n && in[i + 1] == '[') {
                i += 2;
                while (i < n && !((in[i] >= '@' && in[i] <= '~'))) {
                    i++;
                }
            }
            continue;
        }
        out[o++] = in[i];
    }
    return o;
}

/* Zeilenpuffer statisch statt auf dem Stack: Log-Aufrufe kommen auch aus Tasks mit kleinem Stack.
 * Nur benutzt, solange s_busy gehalten wird. */
static char s_line[LINE_MAX_LEN], s_clean[LINE_MAX_LEN];

static int log_vprintf(const char *fmt, va_list args)
{
    if (!atomic_flag_test_and_set(&s_busy)) {   /* belegt (anderer Task/Kern): Zeile verwerfen */
        va_list copy;
        va_copy(copy, args);
        int len = vsnprintf(s_line, sizeof(s_line), fmt, copy);
        va_end(copy);
        if (len > 0) {
            size_t n = len < (int)sizeof(s_line) ? (size_t)len : sizeof(s_line) - 1;
            if (len >= (int)sizeof(s_line)) {
                s_line[sizeof(s_line) - 2] = '\n';  /* abgeschnittene Zeile trotzdem sauber beenden */
            }
            put(s_clean, strip_ansi(s_line, n, s_clean));
        }
        atomic_flag_clear(&s_busy);
    }
    return s_old_vprintf ? s_old_vprintf(fmt, args) : 0;
}

static const char *reset_reason_str(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "Power-on";
    case ESP_RST_EXT: return "extern";
    case ESP_RST_SW: return "Software-Neustart";
    case ESP_RST_PANIC: return "Absturz (Panic)";
    case ESP_RST_INT_WDT: return "Interrupt-Watchdog";
    case ESP_RST_TASK_WDT: return "Task-Watchdog";
    case ESP_RST_WDT: return "Watchdog";
    case ESP_RST_DEEPSLEEP: return "Deep-Sleep";
    case ESP_RST_BROWNOUT: return "Brownout (Spannung zu niedrig)";
    case ESP_RST_USB: return "USB";
    default: return "unbekannt";
    }
}

void log_buf_init(void)
{
    esp_reset_reason_t rr = esp_reset_reason();
    bool valid = s_log.magic == MAGIC && s_log.check == calc_check();
    if (rr == ESP_RST_POWERON || rr == ESP_RST_BROWNOUT || !valid) {
        reset_store();
        valid = false;
    }
    if (!s_old_vprintf) {
        s_old_vprintf = esp_log_set_vprintf(log_vprintf);
    }
    ESP_LOGI("boot", "---- Start: Reset-Grund %s (%d), Firmware %s%s ----", reset_reason_str(rr), (int)rr,
             esp_app_get_description()->version, valid ? "" : ", Log neu");
}

size_t log_buf_read(uint32_t *pos, uint32_t end, char *out, size_t cap)
{
    while (atomic_flag_test_and_set(&s_busy)) {
        vTaskDelay(1);
    }
    uint32_t total = s_log.total;
    if (end < total) {
        total = end;        /* nur bis zum Stand bei Abrufbeginn, sonst endet ein Abruf bei vielen Meldungen nie */
    }
    uint32_t start = total > LOG_SIZE ? total - LOG_SIZE : 0;
    if (*pos < start || *pos > total) {
        *pos = start;
    }
    size_t n = total - *pos;
    if (n > cap) {
        n = cap;
    }
    for (size_t i = 0; i < n; i++) {
        out[i] = s_log.data[(*pos + i) % LOG_SIZE];
    }
    *pos += n;
    atomic_flag_clear(&s_busy);
    return n;
}

void log_buf_clear(void)
{
    while (atomic_flag_test_and_set(&s_busy)) {
        vTaskDelay(1);
    }
    reset_store();
    atomic_flag_clear(&s_busy);
}

uint32_t log_buf_end(void)
{
    return s_log.total;
}

/* ---- Panic: Info in den noinit-Puffer schreiben, bevor der IDF-Handler auf den UART ausgibt ----
 * Läuft im Panic-Zustand: keine Sperren, kein malloc, kein ESP_LOG, kein vsnprintf; s_busy wird ignoriert. */
static void IRAM_ATTR pput_str(const char *s)
{
    if (!s) {
        s = "(null)";
    }
    for (; *s; s++) {
        s_log.data[s_log.total % LOG_SIZE] = *s;
        s_log.total++;
    }
}

static void IRAM_ATTR pput_hex(uint32_t v)
{
    static const char hex[] = "0123456789abcdef";
    char b[10] = "0x";
    for (int i = 0; i < 8; i++) {
        b[2 + i] = hex[(v >> (28 - 4 * i)) & 0xF];
    }
    for (int i = 0; i < 10; i++) {
        s_log.data[s_log.total % LOG_SIZE] = b[i];
        s_log.total++;
    }
}

extern void __real_esp_panic_handler(panic_info_t *info);

void IRAM_ATTR __wrap_esp_panic_handler(panic_info_t *info)
{
    pput_str("\n!!! PANIC core ");
    pput_hex((uint32_t)info->core);
    pput_str(" reason: ");
    pput_str(info->reason);
    pput_str(" / ");
    pput_str(info->description);
    pput_str(" addr: ");
    pput_hex((uint32_t)info->addr);
    if (g_panic_abort) {
        pput_str(" abort: ");
        pput_str(g_panic_abort_details);
    }
    const XtExcFrame *f = (const XtExcFrame *)info->frame;
    if (f) {
        pput_str("\n    PC ");
        pput_hex(f->pc);
        pput_str(" EXCCAUSE ");
        pput_hex(f->exccause);
        pput_str(" EXCVADDR ");
        pput_hex(f->excvaddr);
        pput_str(" A0 ");
        pput_hex(f->a0);
        pput_str(" SP ");
        pput_hex(f->a1);
        pput_str("\n    Backtrace:");
        /* Start im Ausnahme-Rahmen (nicht im Handler selbst), wie der IDF-Backtrace bei einer Panic */
        esp_backtrace_frame_t bt = { .pc = f->pc, .sp = f->a1, .next_pc = f->a0, .exc_frame = f };
        pput_str(" ");
        pput_hex(bt.pc);
        pput_str(":");
        pput_hex(bt.sp);
        for (int i = 1; i < 16 && bt.next_pc != 0 && esp_backtrace_get_next_frame(&bt); i++) {
            pput_str(" ");
            pput_hex(bt.pc);
            pput_str(":");
            pput_hex(bt.sp);
        }
    }
    pput_str("\n");
    s_log.check = calc_check();
    __real_esp_panic_handler(info);
}
