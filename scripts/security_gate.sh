#!/bin/bash
# ==============================================================================
# Program: security_gate.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if [ -x ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    else
        PYTHON="python"
    fi
fi

"$PYTHON" scripts/security_audit.py
"$PYTHON" scripts/dependency_policy.py
"$PYTHON" -m pytest tests -q
"$PYTHON" -m compileall -q app scripts tests
bash -n scripts/*.sh
