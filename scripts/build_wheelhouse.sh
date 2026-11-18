#!/bin/bash
# ==============================================================================
# Program: build_wheelhouse.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================

set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

if [ ! -f requirements-build.in ]; then
    echo "Missing requirements-build.in" >&2
    exit 1
fi

install -d -m 700 wheelhouse
python -m pip download --only-binary=:all: --dest wheelhouse -r requirements-build.in
python scripts/hash_wheelhouse.py
