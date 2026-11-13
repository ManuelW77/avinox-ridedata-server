#!/usr/bin/env bash
# Startet den Avinox-Server lokal als Dev-Server mit Auto-Reload.
#
#   ./dev.sh              -> http://127.0.0.1:8000
#   ./dev.sh --lan        -> auf allen Schnittstellen (z. B. für den ESP32 im WLAN)
#   PORT=8080 ./dev.sh    -> anderer Port
#
# Daten liegen in ./data (überschreibbar mit AVINOX_DATA_DIR).
# Python-Änderungen unter src/ laden den Server neu; Templates und
# static/ werden ohnehin bei jedem Aufruf frisch gelesen (Browser neu laden).
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

if [[ "${1:-}" == "--lan" ]]; then
    HOST="0.0.0.0"
fi

export AVINOX_DATA_DIR="${AVINOX_DATA_DIR:-$PWD/data}"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv fehlt. Installieren mit: brew install uv" >&2
    exit 1
fi

uv sync --quiet

echo "Avinox Dev-Server: http://${HOST}:${PORT}  (Daten: ${AVINOX_DATA_DIR})"
[[ "$HOST" == "0.0.0.0" ]] && echo "Im LAN erreichbar unter: http://$(ipconfig getifaddr en0 2>/dev/null || hostname):${PORT}"

exec uv run uvicorn avinox.server.app:create_app --factory \
    --host "$HOST" --port "$PORT" \
    --reload --reload-dir src
