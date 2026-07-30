#!/bin/bash
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
    echo "Usage: scripts/sign_release.sh <artifact> [artifact ...]" >&2
    exit 1
fi

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
        openssl dgst -sha256 -sign "$ID_APP_SIGNING_KEY" -out "$artifact.sig" "$artifact"
        openssl pkey -in "$ID_APP_SIGNING_KEY" -pubout -out "$artifact.pub.pem"
        openssl dgst -sha256 -verify "$artifact.pub.pem" -signature "$artifact.sig" "$artifact"
    done
    exit 0
fi

if ! command -v gpg >/dev/null 2>&1; then
    echo "gpg is required for release signing unless ID_APP_SIGNING_KEY is set." >&2
    exit 1
fi

for artifact in "$@"; do
    if [ ! -f "$artifact" ]; then
        echo "Missing artifact: $artifact" >&2
        exit 1
    fi
    gpg --batch --yes --armor --detach-sign "$artifact"
    gpg --batch --verify "$artifact.asc" "$artifact"
done
