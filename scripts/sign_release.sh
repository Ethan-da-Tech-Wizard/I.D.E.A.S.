#!/bin/bash
# ==============================================================================
# Program: sign_release.sh
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Shell script automating operational deployment, release verification, dependency sandboxing, or build packaging.
# ==============================================================================
#
# Purpose: create detached signatures for release artifacts so verify_release.sh can authenticate
# them. Two modes, chosen by environment:
#   * ID_APP_SIGNING_KEY=/path/key -> OpenSSL RSA/SHA-256; writes <artifact>.sig and <artifact>.pub.pem.
#   * otherwise GPG detached ASCII signature; writes <artifact>.asc.
# Usage  : scripts/sign_release.sh <artifact> [artifact ...]
# Each signature is verified immediately after creation, so a mis-signed file fails the build
# instead of failing later on a user's machine.

# Strict mode: exit on any error (-e), on unset variables (-u) and on failures inside pipes.
# A release script that keeps going after a failed step could ship an unverified build.
set -euo pipefail
# New files default to owner-only (rw-------): keys, signatures and the install dir stay private.
umask 077

# Run from the repo root regardless of where the script was invoked, so relative paths below hold.
cd "$(dirname "$0")/.."

# At least one artifact is required; signing nothing should not look like success.
if [ $# -lt 1 ]; then
    echo "Usage: scripts/sign_release.sh <artifact> [artifact ...]" >&2
    exit 1
fi

# Mode 1: organisation key from create_org_signing_key.sh (the controlled release machine).
if [ -n "${ID_APP_SIGNING_KEY:-}" ]; then
    if ! command -v openssl >/dev/null 2>&1; then
        echo "openssl is required for ID_APP_SIGNING_KEY signing." >&2
        exit 1
    fi
    if [ ! -f "$ID_APP_SIGNING_KEY" ]; then
        echo "Missing signing key: $ID_APP_SIGNING_KEY" >&2
        exit 1
    fi
    for artifact in "$@"; do
        if [ ! -f "$artifact" ]; then
            echo "Missing artifact: $artifact" >&2
            exit 1
        fi
        # Sign, publish the matching public key beside it, then verify with that public key
        # (the same check an installer performs) before moving on.
        openssl dgst -sha256 -sign "$ID_APP_SIGNING_KEY" -out "$artifact.sig" "$artifact"
        openssl pkey -in "$ID_APP_SIGNING_KEY" -pubout -out "$artifact.pub.pem"
        openssl dgst -sha256 -verify "$artifact.pub.pem" -signature "$artifact.sig" "$artifact"
    done
    # Key mode finished; do not fall through to GPG.
    exit 0
fi

# Mode 2: fall back to the signer's GPG keyring (developer machines).

if ! command -v gpg >/dev/null 2>&1; then
    echo "gpg is required for release signing unless ID_APP_SIGNING_KEY is set." >&2
    exit 1
fi

for artifact in "$@"; do
    if [ ! -f "$artifact" ]; then
        echo "Missing artifact: $artifact" >&2
        exit 1
    fi
    # --batch/--yes: no prompts, overwrite an old .asc; the verify step proves the signature is good.
    gpg --batch --yes --armor --detach-sign "$artifact"
    gpg --batch --verify "$artifact.asc" "$artifact"
done
