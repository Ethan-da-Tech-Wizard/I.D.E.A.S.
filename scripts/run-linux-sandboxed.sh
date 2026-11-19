#!/bin/bash
# ==============================================================================
# Program: run-linux-sandboxed.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: run the app inside a bubblewrap (bwrap) container so a compromised or buggy build cannot
# reach the network or the user's files. Called by launch-linux.sh; takes the binary path as $1.
# Environment: ID_APP_DATA_DIR sets the only writable, persistent folder (default
# ~/.local/share/NeonDAW, where projects and settings live).
# Sandbox policy: no network namespace access, read-only system libraries, private /tmp, a fresh
# /dev and /proc, and nothing from $HOME except the data folder.
# Known limits: needs bubblewrap installed; the /lib64 and /etc/fonts binds assume a
# Debian/Fedora-style layout and bwrap fails if one of them is absent.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail

# Defaults mirror install-linux.sh; the data dir is the one place the app may persist state.
APP_NAME="id_app"
APP_BIN="${1:-$HOME/.local/opt/$APP_NAME/$APP_NAME}"
APP_DATA="${ID_APP_DATA_DIR:-$HOME/.local/share/NeonDAW}"

# Refuse rather than silently run unconfined.
if ! command -v bwrap >/dev/null 2>&1; then
    echo "bubblewrap is required for sandboxed launch." >&2
    exit 1
fi

# Catch a wrong path here for a clearer message than bwrap's own error.
if [ ! -x "$APP_BIN" ]; then
    echo "Executable not found or not executable: $APP_BIN" >&2
    exit 1
fi

# The data folder must exist on the host before it can be bind-mounted writable.
install -d -m 700 "$APP_DATA"

# Flag by flag:
#  --unshare-net     new empty network namespace: the app has no connectivity at all.
#  --die-with-parent kill the app if the launcher dies, leaving no orphaned process.
#  --ro-bind ...     expose system libraries and fonts read-only; the app cannot alter them.
#  --dev/--proc      minimal fresh device and process views instead of the host's.
#  --tmpfs /tmp      private scratch space that vanishes on exit.
#  --bind APP_DATA   the single writable path, mapped to the same location so saved paths match.
#  --ro-bind APP_BIN the executable itself is read-only inside the sandbox.
#  --setenv          tells the app where its data folder is (app/security.py reads it).
exec bwrap \
    --unshare-net \
    --die-with-parent \
    --ro-bind /usr /usr \
    --ro-bind /lib /lib \
    --ro-bind /lib64 /lib64 \
    --ro-bind /bin /bin \
    --ro-bind /etc/fonts /etc/fonts \
    --dev /dev \
    --proc /proc \
    --tmpfs /tmp \
    --bind "$APP_DATA" "$APP_DATA" \
    --ro-bind "$APP_BIN" "$APP_BIN" \
    --setenv ID_APP_DATA_DIR "$APP_DATA" \
    "$APP_BIN"
