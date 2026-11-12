/*
 * USB-Host + Massenspeicher, übernommen aus esp32/usb-probe/ (am Display getestet, ca. 845 KB/s).
 * Hintergrund zum MPS-512-Problem: ERKENNTNISSE.md Abschnitt 3b und components/usb/PATCH.md.
 */
#include "usb_drive.h"

#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "usb/usb_host.h"
#include "usb/msc_host_vfs.h"

static const char *TAG = "usb";

/*
 * Eigene FIFO-Aufteilung (Einheit: Zeilen à 4 Byte). Der ESP32-S3 (FS-PHY) hat 200 Zeilen.
 *   IN = (RX - 2) * 4, NPTX = NPTX * 4, PTX = PTX * 4
 * Default (RX 104 / NPTX 64 / PTX 32) erlaubt nur IN 408 – das Display meldet Bulk-IN MPS 512.
 * NPTX 16 (64 Byte) reicht für CBWs und EP0, PTX 0 (MSC hat keine periodischen Endpunkte).
 */
#define FIFO_TOTAL_LINES   200
#define FIFO_NPTX_LINES    16
#define FIFO_PTX_LINES     0
#define FIFO_RX_LINES      (FIFO_TOTAL_LINES - FIFO_NPTX_LINES - FIFO_PTX_LINES)
_Static_assert((FIFO_RX_LINES - 2) * 4 >= 512, "RX-FIFO zu klein für IN-MPS 512");

typedef struct {
    enum { EV_CONNECTED, EV_DISCONNECTED } id;
    union {
        uint8_t address;
        msc_host_device_handle_t handle;
    } data;
} usb_msg_t;

static QueueHandle_t s_queue;
static SemaphoreHandle_t s_lock;            /* schützt s_dev / s_vfs */
static msc_host_device_handle_t s_dev;
static msc_host_vfs_handle_t s_vfs;
static volatile drive_state_t s_state = DRIVE_ABSENT;
static volatile bool s_host_failed;
static volatile uint32_t s_generation;

static void msc_event_cb(const msc_host_event_t *event, void *arg)
{
    usb_msg_t msg;
    if (event->event == MSC_DEVICE_CONNECTED) {
        msg.id = EV_CONNECTED;
        msg.data.address = event->device.address;
    } else if (event->event == MSC_DEVICE_DISCONNECTED) {
        msg.id = EV_DISCONNECTED;
        msg.data.handle = event->device.handle;
    } else {
        ESP_LOGW(TAG, "Unbekanntes MSC-Ereignis %d", event->event);
        return;
    }
    if (xQueueSend(s_queue, &msg, pdMS_TO_TICKS(1000)) != pdTRUE) {
        ESP_LOGE(TAG, "USB-Ereignis verloren (Queue voll)");
    }
}

static void host_task(void *arg)
{
    const usb_host_config_t host_config = {
        .intr_flags = ESP_INTR_FLAG_LOWMED,
        .fifo_settings_custom = {
            .rx_fifo_lines = FIFO_RX_LINES,
            .nptx_fifo_lines = FIFO_NPTX_LINES,
            .ptx_fifo_lines = FIFO_PTX_LINES,
        },
    };
    esp_err_t err = usb_host_install(&host_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb_host_install fehlgeschlagen (FIFO RX=%d NPTX=%d PTX=%d): %s",
                 FIFO_RX_LINES, FIFO_NPTX_LINES, FIFO_PTX_LINES, esp_err_to_name(err));
        s_host_failed = true;
        vTaskDelete(NULL);
        return;
    }
    const msc_host_driver_config_t msc_config = {
        .create_backround_task = true,
        .task_priority = 5,
        .stack_size = 4096,
        .callback = msc_event_cb,
    };
    err = msc_host_install(&msc_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "msc_host_install fehlgeschlagen: %s", esp_err_to_name(err));
        usb_host_uninstall();
        s_host_failed = true;
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "USB-Host und MSC-Treiber installiert (FIFO RX=%d NPTX=%d PTX=%d)",
             FIFO_RX_LINES, FIFO_NPTX_LINES, FIFO_PTX_LINES);
    while (true) {
        uint32_t flags;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
        if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) {
            usb_host_device_free_all();
        }
    }
}

static void wchar_to_ascii(const wchar_t *in, char *out, size_t len)
{
    size_t i = 0;
    for (; i + 1 < len && in[i]; i++) {
        out[i] = (in[i] >= 0x20 && in[i] < 0x7f) ? (char)in[i] : '?';
    }
    out[i] = '\0';
}

static void on_connected(uint8_t addr)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_dev) {
        xSemaphoreGive(s_lock);
        ESP_LOGW(TAG, "Weiteres USB-Gerät ignoriert");
        return;
    }
    msc_host_device_handle_t dev = NULL;
    esp_err_t err = msc_host_install_device(addr, &dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Anmeldung des Laufwerks fehlgeschlagen: %s", esp_err_to_name(err));
        s_state = DRIVE_FAILED;
        xSemaphoreGive(s_lock);
        return;
    }
    s_dev = dev;
    msc_host_device_info_t info;
    if (msc_host_get_device_info(dev, &info) == ESP_OK) {
        char manu[64], prod[64];
        wchar_to_ascii(info.iManufacturer, manu, sizeof(manu));
        wchar_to_ascii(info.iProduct, prod, sizeof(prod));
        /* Seriennummer bewusst nicht loggen */
        ESP_LOGI(TAG, "Laufwerk: VID=0x%04X PID=0x%04X '%s' / '%s', %llu MB",
                 info.idVendor, info.idProduct, manu, prod,
                 ((uint64_t)info.sector_size * info.sector_count) / (1024 * 1024));
    }
    s_generation++;
    s_state = DRIVE_READY;
    xSemaphoreGive(s_lock);
}

static void on_disconnected(msc_host_device_handle_t handle)
{
    ESP_LOGI(TAG, "USB-Gerät abgezogen");
    drive_state_t prev = s_state;
    s_state = DRIVE_ABSENT;     /* zuerst, damit ein laufender Sync abbricht */
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_dev && s_dev == handle) {
        if (s_vfs) {
            msc_host_vfs_unregister(s_vfs);
            s_vfs = NULL;
        }
        esp_err_t err = msc_host_uninstall_device(s_dev);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "msc_host_uninstall_device: %s", esp_err_to_name(err));
        }
        s_dev = NULL;
    } else if (s_dev) {
        s_state = prev;         /* anderes Gerät abgezogen, unseres bleibt */
    }
    xSemaphoreGive(s_lock);
}

static void drive_task(void *arg)
{
    while (true) {
        usb_msg_t msg;
        if (xQueueReceive(s_queue, &msg, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (msg.id == EV_CONNECTED) {
            on_connected(msg.data.address);
        } else {
            on_disconnected(msg.data.handle);
        }
    }
}

esp_err_t usb_drive_start(void)
{
    s_queue = xQueueCreate(5, sizeof(usb_msg_t));
    s_lock = xSemaphoreCreateMutex();
    if (!s_queue || !s_lock) {
        s_host_failed = true;
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(host_task, "usb_host", 4096, NULL, 2, NULL) != pdPASS ||
            xTaskCreate(drive_task, "usb_drive", 4096, NULL, 2, NULL) != pdPASS) {
        s_host_failed = true;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool usb_drive_host_failed(void)
{
    return s_host_failed;
}

drive_state_t usb_drive_state(void)
{
    return s_state;
}

uint32_t usb_drive_generation(void)
{
    return s_generation;
}

esp_err_t usb_drive_mount(void)
{
    if (!s_lock) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (!s_dev || s_state != DRIVE_READY) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_NOT_FOUND;
    }
    const esp_vfs_fat_mount_config_t mount_config = {
        .format_if_mount_failed = false,   /* niemals formatieren */
        .max_files = 3,
        .allocation_unit_size = 0,
    };
    esp_err_t err = msc_host_vfs_register(s_dev, USB_MNT_PATH, &mount_config, &s_vfs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Einhängen unter %s fehlgeschlagen: %s", USB_MNT_PATH, esp_err_to_name(err));
        s_vfs = NULL;
        xSemaphoreGive(s_lock);
        return err;
    }
    /* Sperre bleibt bis usb_drive_unmount() */
    return ESP_OK;
}

void usb_drive_unmount(void)
{
    /* Wird nur nach erfolgreichem usb_drive_mount() aufgerufen, die Sperre gehört uns. */
    if (s_vfs) {
        esp_err_t err = msc_host_vfs_unregister(s_vfs);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "Aushängen: %s", esp_err_to_name(err));
        }
        s_vfs = NULL;
    }
    xSemaphoreGive(s_lock);
}
