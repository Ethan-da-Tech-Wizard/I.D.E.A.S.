#!/bin/bash
# ==============================================================================
# Program: launch-linux.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: the command users actually run (symlinked as ~/.local/bin/id_app). Starts the app inside
# the bubblewrap sandbox when available and refuses to start it unsandboxed unless the user opts in.
# Environment: ID_APP_BIN overrides the binary path; ID_APP_ALLOW_UNSANDBOXED=1 permits running
# without bwrap (for development machines that cannot install it).
# Calls  : run-linux-sandboxed.sh.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail

# ID_APP_BIN lets tests or developers point at a different build.
APP_NAME="id_app"
APP_BIN="${ID_APP_BIN:-$HOME/.local/opt/$APP_NAME/$APP_NAME}"
# Resolve the real directory of this script (symlink-safe) to find the sandbox runner beside it.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SANDBOX_RUNNER="$SCRIPT_DIR/run-linux-sandboxed.sh"

# Preferred path: sandbox available -> hand over to it. exec replaces this shell, so no
# wrapper process lingers and signals reach the app directly.
if command -v bwrap >/dev/null 2>&1 && [ -x "$SANDBOX_RUNNER" ]; then
    exec "$SANDBOX_RUNNER" "$APP_BIN"
fi

# Fail closed: without bwrap the app would have full user-level file and network access, so
# running that way needs an explicit, visible opt-in.
if [ "${ID_APP_ALLOW_UNSANDBOXED:-0}" != "1" ]; then
    echo "Refusing unsandboxed launch. Install bubblewrap or set ID_APP_ALLOW_UNSANDBOXED=1." >&2
    exit 1
fi

# Explicit opt-in granted: run the binary directly.
exec "$APP_BIN"
