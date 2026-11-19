#!/bin/bash
# ==============================================================================
# Program: verify_release.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: authenticate release artifacts before they are installed or published. For each file it
# looks for either an OpenSSL signature pair (<f>.sig + <f>.pub.pem) or a GPG signature (<f>.asc)
# and verifies it. Called by install-linux.sh; also run by hand on downloaded releases.
# Usage  : scripts/verify_release.sh <artifact> [artifact ...]
# Fail-closed: a missing artifact, missing signature, or missing tool is an error, never a skip.
# Known limit: an embedded .pub.pem proves integrity against that key only; compare its
# fingerprint with the organisation's published key to prove who signed.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail

# Run from the repo root regardless of where the script was invoked, so relative paths below hold.
cd "$(dirname "$0")/.."

# Verifying zero files must not report success.
if [ $# -lt 1 ]; then
    echo "Usage: scripts/verify_release.sh <artifact> [artifact ...]" >&2
    exit 1
fi

for artifact in "$@"; do
    if [ ! -f "$artifact" ]; then
        echo "Missing artifact: $artifact" >&2
        exit 1
    fi
    # Prefer the OpenSSL pair (key-file mode of sign_release.sh), then GPG; no signature at all is fatal.
    if [ -f "$artifact.sig" ] && [ -f "$artifact.pub.pem" ]; then
        if ! command -v openssl >/dev/null 2>&1; then
            echo "openssl is required to verify $artifact.sig" >&2
            exit 1
        fi
        # openssl exits non-zero on a bad signature, and set -e then aborts the whole run.
        openssl dgst -sha256 -verify "$artifact.pub.pem" -signature "$artifact.sig" "$artifact"
    elif [ -f "$artifact.asc" ]; then
        if ! command -v gpg >/dev/null 2>&1; then
            echo "gpg is required to verify $artifact.asc" >&2
            exit 1
        fi
        # --batch: never prompt, so CI cannot hang waiting for input.
        gpg --batch --verify "$artifact.asc" "$artifact"
    else
        echo "Missing signature for: $artifact" >&2
        exit 1
    fi
done
