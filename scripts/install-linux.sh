#!/bin/bash
# ==============================================================================
# Program: install-linux.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: per-user installer for the Linux release. Verifies the signed build first, copies it
# under ~/.local/opt/id_app, adds a launcher on PATH and a desktop entry. Needs no root and writes
# nothing outside $HOME.
# Inputs : dist/id_app, dist/id_app.sbom.json plus their signatures (from build-linux.sh).
# Outputs: ~/.local/opt/id_app/*, ~/.local/bin/id_app (symlink), ~/.local/share/applications entry.
# Counterpart: uninstall-linux.sh removes everything created here.
# Known limit: the desktop entry always uses the sandbox-aware launch-linux.sh.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail
# New files default to owner-only (rw-------): keys, signatures and the install dir stay private.
umask 077

# Paths follow the XDG per-user layout so no sudo is needed and uninstall-linux.sh can mirror them.
APP_NAME="id_app"
DEST_DIR="$HOME/.local/opt/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
# Release inputs produced by build-linux.sh, relative to the repo root.
SOURCE="dist/$APP_NAME"
SBOM="dist/$APP_NAME.sbom.json"

echo "Installing $APP_NAME..."
# Run from the repo root regardless of where the script was invoked, so relative paths below hold.
cd "$(dirname "$0")/.."

# Both the binary and its SBOM must exist: installing without the SBOM would skip the audit trail.
if [ ! -f "$SOURCE" ]; then
    echo "Missing release executable: $SOURCE"
    exit 1
fi
if [ ! -f "$SBOM" ]; then
    echo "Missing release SBOM: $SBOM"
    exit 1
fi

# Verify signatures BEFORE copying anything; set -e aborts the install if either check fails.
scripts/verify_release.sh "$SOURCE" "$SBOM"

# Private (700) directories: other local users cannot read or replace the installed binary.
install -d -m 700 "$DEST_DIR"
install -d -m 700 "$BIN_DIR"
install -d -m 700 "$DESKTOP_DIR"

# Copy the binary with its signature material so the install can be re-verified later.
# Public keys are 644 (not secret); signatures and SBOM stay 600.
install -m 700 "$SOURCE" "$DEST_DIR/$APP_NAME"
install -m 600 "$SOURCE.sig" "$DEST_DIR/$APP_NAME.sig"
install -m 644 "$SOURCE.pub.pem" "$DEST_DIR/$APP_NAME.pub.pem"
install -m 600 "$SBOM" "$DEST_DIR/$APP_NAME.sbom.json"
install -m 600 "$SBOM.sig" "$DEST_DIR/$APP_NAME.sbom.json.sig"
install -m 644 "$SBOM.pub.pem" "$DEST_DIR/$APP_NAME.sbom.json.pub.pem"
# The launchers are installed next to the binary so the symlink target is self-contained.
install -m 700 scripts/run-linux-sandboxed.sh "$DEST_DIR/run-linux-sandboxed.sh"
install -m 700 scripts/launch-linux.sh "$DEST_DIR/launch-linux.sh"
# Put `id_app` on PATH through a symlink to the launcher (not the raw binary) to keep the sandbox.
ln -sf "$DEST_DIR/launch-linux.sh" "$BIN_DIR/$APP_NAME"

# Create the file with mode 600 first so it never exists with looser permissions, then fill it.
# (Comments cannot go inside the heredoc below: they would become part of the .desktop file.)
install -m 600 /dev/null "$DESKTOP_DIR/$APP_NAME.desktop"
cat > "$DESKTOP_DIR/$APP_NAME.desktop" << EOF
[Desktop Entry]
Name=I.D. App
Exec=$DEST_DIR/launch-linux.sh
Type=Application
Terminal=false
Categories=Utility;
EOF
# Re-assert 600 in case the redirect above changed anything.
chmod 600 "$DESKTOP_DIR/$APP_NAME.desktop"

# Refresh the desktop menu cache when the tool exists; it is optional on minimal systems.

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$DESKTOP_DIR"
fi

echo "Installation complete."
