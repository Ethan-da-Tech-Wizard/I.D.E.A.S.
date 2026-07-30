#!/bin/bash
set -euo pipefail
umask 077

APP_NAME="id_app"
DEST_DIR="$HOME/.local/opt/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
SOURCE="dist/$APP_NAME"
SBOM="dist/$APP_NAME.sbom.json"

echo "Installing $APP_NAME..."
cd "$(dirname "$0")/.."

if [ ! -f "$SOURCE" ]; then
    echo "Missing release executable: $SOURCE"
    exit 1
fi
if [ ! -f "$SBOM" ]; then
    echo "Missing release SBOM: $SBOM"
    exit 1
fi

scripts/verify_release.sh "$SOURCE" "$SBOM"

install -d -m 700 "$DEST_DIR"
install -d -m 700 "$BIN_DIR"
install -d -m 700 "$DESKTOP_DIR"

install -m 700 "$SOURCE" "$DEST_DIR/$APP_NAME"
install -m 600 "$SOURCE.sig" "$DEST_DIR/$APP_NAME.sig"
install -m 644 "$SOURCE.pub.pem" "$DEST_DIR/$APP_NAME.pub.pem"
install -m 600 "$SBOM" "$DEST_DIR/$APP_NAME.sbom.json"
install -m 600 "$SBOM.sig" "$DEST_DIR/$APP_NAME.sbom.json.sig"
install -m 644 "$SBOM.pub.pem" "$DEST_DIR/$APP_NAME.sbom.json.pub.pem"
install -m 700 scripts/run-linux-sandboxed.sh "$DEST_DIR/run-linux-sandboxed.sh"
install -m 700 scripts/launch-linux.sh "$DEST_DIR/launch-linux.sh"
ln -sf "$DEST_DIR/launch-linux.sh" "$BIN_DIR/$APP_NAME"

install -m 600 /dev/null "$DESKTOP_DIR/$APP_NAME.desktop"
cat > "$DESKTOP_DIR/$APP_NAME.desktop" << EOF
[Desktop Entry]
Name=I.D. App
Exec=$DEST_DIR/launch-linux.sh
Type=Application
Terminal=false
Categories=Utility;
EOF
chmod 600 "$DESKTOP_DIR/$APP_NAME.desktop"

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$DESKTOP_DIR"
fi

echo "Installation complete."
