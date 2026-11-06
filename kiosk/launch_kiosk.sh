#!/usr/bin/env bash
# ==============================================================================
# Script de inicio para Homarr Kiosk en Raspberry Pi OS Bookworm
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"

# Localizar archivo .env
ENV_FILE=""
for f in "$BASE_DIR/.env" "/etc/idrac-silent-boot/.env" "$HOME/.env"; do
    if [ -f "$f" ]; then
        ENV_FILE="$f"
        break
    fi
done

HOMARR_URL="http://192.168.1.100:7575"
if [ -n "$ENV_FILE" ]; then
    VAL=$(grep -E '^HOMARR_URL=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '"' | tr -d "'")
    if [ -n "$VAL" ]; then
        HOMARR_URL="$VAL"
    fi
fi

# Detectar comando de Chromium
CHROME_BIN=""
for bin in chromium-browser chromium google-chrome; do
    if command -v "$bin" &>/dev/null; then
        CHROME_BIN="$bin"
        break
    fi
done

if [ -z "$CHROME_BIN" ]; then
    echo "[ERROR] No se encontró un binario de Chromium/Chrome instalado." >&2
    exit 1
fi

SPLASH_PATH="$SCRIPT_DIR/splash.html"
TARGET_URL="file://$SPLASH_PATH?url=$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$HOMARR_URL" 2>/dev/null || echo "$HOMARR_URL")"

# Opciones de Kiosk optimizadas para Raspberry Pi
FLAGS=(
    --kiosk
    --noerrdialogs
    --disable-infobars
    --check-for-update-interval=31536000
    --disable-session-crashed-bubble
    --disable-features=TranslateUI
    --no-first-run
    --fast
    --fast-start
    --disable-pinch
    --overscroll-history-navigation=0
    --allow-file-access-from-files
    --autoplay-policy=no-user-gesture-required
    --password-store=basic
)

# Ocultar cursor del mouse tras inactividad si unclutter está instalado
if command -v unclutter &>/dev/null; then
    unclutter -idle 2 &
fi

exec "$CHROME_BIN" "${FLAGS[@]}" "$TARGET_URL"
