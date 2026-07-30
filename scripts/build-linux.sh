#!/bin/bash
set -euo pipefail
umask 077

echo "Building Linux Release..."
cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    echo "Missing .venv. Create a reviewed local build environment first."
    exit 1
fi

source .venv/bin/activate
if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "PyInstaller is not installed in .venv."
    echo "Install reviewed, pinned build dependencies before running this release build."
    exit 1
fi

pyinstaller \
    --name id_app \
    --windowed \
    --onefile \
    --exclude-module PySide6.QtDBus \
    --exclude-module PySide6.QtQml \
    --exclude-module PySide6.QtQuick \
    --exclude-module PySide6.QtWebEngineCore \
    --exclude-module PySide6.QtWebEngineWidgets \
    --exclude-module PySide6.QtWebView \
    app/main.py
python scripts/generate_sbom.py
scripts/sign_release.sh dist/id_app dist/id_app.sbom.json
echo "Build complete. Executable is in dist/id_app"
