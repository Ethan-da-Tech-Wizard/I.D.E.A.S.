#!/bin/bash
# ==============================================================================
# Program: uninstall-linux.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: remove everything install-linux.sh created for the current user: the install folder,
# the PATH symlink and the desktop entry. Mirrors the install paths exactly.
# Not removed on purpose: user projects and settings in ~/.local/share/NeonDAW (the app data
# folder), so uninstalling never deletes someone's music or code.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail

# Same locations as install-linux.sh (the *_DIR names here hold file paths for the symlink/entry).
APP_NAME="id_app"
DEST_DIR="$HOME/.local/opt/$APP_NAME"
BIN_DIR="$HOME/.local/bin/$APP_NAME"
DESKTOP_DIR="$HOME/.local/share/applications/$APP_NAME.desktop"

echo "Uninstalling $APP_NAME..."
# -f makes each removal idempotent: running twice, or after a partial install, is fine.
rm -rf "$DEST_DIR"
rm -f "$BIN_DIR"
rm -f "$DESKTOP_DIR"

# Refresh the menu cache so the launcher entry disappears right away (tool is optional).
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications"
fi

echo "Uninstallation complete."
