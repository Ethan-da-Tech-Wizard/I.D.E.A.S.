#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
    echo "Usage: scripts/verify_release.sh <artifact> [artifact ...]" >&2
    exit 1
fi

for artifact in "$@"; do
    if [ ! -f "$artifact" ]; then
        echo "Missing artifact: $artifact" >&2
        exit 1
    fi
    if [ -f "$artifact.sig" ] && [ -f "$artifact.pub.pem" ]; then
        if ! command -v openssl >/dev/null 2>&1; then
            echo "openssl is required to verify $artifact.sig" >&2
            exit 1
        fi
        openssl dgst -sha256 -verify "$artifact.pub.pem" -signature "$artifact.sig" "$artifact"
    elif [ -f "$artifact.asc" ]; then
        if ! command -v gpg >/dev/null 2>&1; then
            echo "gpg is required to verify $artifact.asc" >&2
            exit 1
        fi
        gpg --batch --verify "$artifact.asc" "$artifact"
    else
        echo "Missing signature for: $artifact" >&2
        exit 1
    fi
done
