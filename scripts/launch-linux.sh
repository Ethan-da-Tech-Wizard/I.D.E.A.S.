#!/bin/bash
set -euo pipefail

APP_NAME="id_app"
APP_BIN="${ID_APP_BIN:-$HOME/.local/opt/$APP_NAME/$APP_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SANDBOX_RUNNER="$SCRIPT_DIR/run-linux-sandboxed.sh"

if command -v bwrap >/dev/null 2>&1 && [ -x "$SANDBOX_RUNNER" ]; then
    exec "$SANDBOX_RUNNER" "$APP_BIN"
fi

if [ "${ID_APP_ALLOW_UNSANDBOXED:-0}" != "1" ]; then
    echo "Refusing unsandboxed launch. Install bubblewrap or set ID_APP_ALLOW_UNSANDBOXED=1." >&2
    exit 1
fi

exec "$APP_BIN"
