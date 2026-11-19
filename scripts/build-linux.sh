#!/bin/bash
# ==============================================================================
# Program: build-linux.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: build the single-file Linux release executable (dist/id_app), then produce its SBOM and
# detached signatures. Part of the release chain: build_wheelhouse.sh -> this -> verify_release.sh
# -> install-linux.sh.
# Inputs : a reviewed .venv containing pinned build dependencies (PyInstaller).
# Outputs: dist/id_app, dist/id_app.sbom.json and their .sig/.pub.pem (or .asc) files.
# Known limits: Linux only; refuses to run without a .venv so an unreviewed system Python is never
# used for a release.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail
# New files default to owner-only (rw-------): keys, signatures and the install dir stay private.
umask 077

echo "Building Linux Release..."
# Run from the repo root regardless of where the script was invoked, so relative paths below hold.
cd "$(dirname "$0")/.."

# Only build from the reviewed virtualenv, never whatever Python happens to be on PATH.
if [ ! -d ".venv" ]; then
    echo "Missing .venv. Create a reviewed local build environment first."
    exit 1
fi

# Activate so `pyinstaller` and `python` below resolve to the pinned build environment.
source .venv/bin/activate
# PyInstaller is a build tool, not an app dependency, so it may legitimately be missing.
if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "PyInstaller is not installed in .venv."
    echo "Install reviewed, pinned build dependencies before running this release build."
    exit 1
fi

# --onefile gives one auditable artifact to hash and sign; --windowed because it is a GUI app.
# The --exclude-module lines drop Qt modules the app never uses (D-Bus, QML/Quick, every web
# engine): less shipped code, and no embedded browser, matching the local-only security policy.
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
# SBOM is generated after the build so it describes the environment that produced the binary.
python scripts/generate_sbom.py
# Sign both artifacts so install-linux.sh/verify_release.sh can prove they were not altered.
scripts/sign_release.sh dist/id_app dist/id_app.sbom.json
echo "Build complete. Executable is in dist/id_app"
