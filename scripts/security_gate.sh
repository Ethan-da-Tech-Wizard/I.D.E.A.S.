#!/bin/bash
# ==============================================================================
# Program: security_gate.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: the local "everything must pass" gate run before a release or push. It is the
# shell equivalent of the CI checks: static security audit, dependency policy, the full test
# suite, byte-compilation of all Python, and a syntax check of every shell script.
# Environment: PYTHON overrides the interpreter; otherwise .venv/bin/python, then `python`.
# Exit status: first failing step (set -e), so a non-zero exit means do not release.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail

# Run from the repo root regardless of where the script was invoked, so relative paths below hold.
cd "$(dirname "$0")/.."

# Interpreter choice: explicit override > project venv (matches release builds) > system python.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if [ -x ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    else
        PYTHON="python"
    fi
fi

# 1. Banned APIs/URLs/markers (see security_audit.py).
"$PYTHON" scripts/security_audit.py
# 2. Only reviewed release dependencies.
"$PYTHON" scripts/dependency_policy.py
# 3. Behaviour tests; -q keeps the log short so failures stand out.
"$PYTHON" -m pytest tests -q
# 4. Catches syntax errors in files no test imports.
"$PYTHON" -m compileall -q app scripts tests
# 5. Parse-only check of every shell script (no execution) so a typo cannot ship in an installer.
bash -n scripts/*.sh
