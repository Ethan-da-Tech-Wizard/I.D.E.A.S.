#!/bin/bash
set -euo pipefail

APP_NAME="id_app"
DEST_DIR="$HOME/.local/opt/$APP_NAME"
BIN_DIR="$HOME/.local/bin/$APP_NAME"
DESKTOP_DIR="$HOME/.local/share/applications/$APP_NAME.desktop"

echo "Uninstalling $APP_NAME..."
rm -rf "$DEST_DIR"
rm -f "$BIN_DIR"
rm -f "$DESKTOP_DIR"

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications"
fi

echo "Uninstallation complete."
