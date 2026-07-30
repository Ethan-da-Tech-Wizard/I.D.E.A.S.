#!/bin/bash
set -euo pipefail

APP_NAME="id_app"
APP_BIN="${1:-$HOME/.local/opt/$APP_NAME/$APP_NAME}"
APP_DATA="${ID_APP_DATA_DIR:-$HOME/.local/share/NeonDAW}"

if ! command -v bwrap >/dev/null 2>&1; then
    echo "bubblewrap is required for sandboxed launch." >&2
    exit 1
fi

if [ ! -x "$APP_BIN" ]; then
    echo "Executable not found or not executable: $APP_BIN" >&2
    exit 1
fi

install -d -m 700 "$APP_DATA"

exec bwrap \
    --unshare-net \
    --die-with-parent \
    --ro-bind /usr /usr \
    --ro-bind /lib /lib \
    --ro-bind /lib64 /lib64 \
    --ro-bind /bin /bin \
    --ro-bind /etc/fonts /etc/fonts \
    --dev /dev \
    --proc /proc \
    --tmpfs /tmp \
    --bind "$APP_DATA" "$APP_DATA" \
    --ro-bind "$APP_BIN" "$APP_BIN" \
    --setenv ID_APP_DATA_DIR "$APP_DATA" \
    "$APP_BIN"
