# main hängt ohne REQUIRES implizit von allen Komponenten ab (esp_http_server, mbedtls, esp_wifi ...)
idf_component_register(SRCS "main.c" "config.c" "portal.c" "wifi_sta.c" "usb_drive.c"
                            "sync.c" "report.c" "sync_paths.c" "led.c" "ota.c"
                            "web_common.c" "web_auth.c" "webui.c"
                       INCLUDE_DIRS ".")
