#!/bin/bash
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

if [ $# -ne 1 ]; then
    echo "Usage: scripts/create_org_signing_key.sh /secure/off-repo/id-app-release.key" >&2
    exit 1
fi

KEY_PATH="$1"
case "$KEY_PATH" in
    /*) ;;
    *)
        echo "Signing key path must be absolute and outside this repository." >&2
        exit 1
        ;;
esac

REPO_ROOT="$(pwd -P)"
KEY_DIR="$(dirname "$KEY_PATH")"
mkdir -p "$KEY_DIR"
KEY_DIR_REAL="$(cd "$KEY_DIR" && pwd -P)"

case "$KEY_DIR_REAL" in
    "$REPO_ROOT"|"$REPO_ROOT"/*)
        echo "Refusing to create a production signing key inside the repository." >&2
        exit 1
        ;;
esac

if [ -e "$KEY_PATH" ]; then
    echo "Refusing to overwrite existing key: $KEY_PATH" >&2
    exit 1
fi

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out "$KEY_PATH"
chmod 600 "$KEY_PATH"
openssl pkey -in "$KEY_PATH" -pubout -out "$KEY_PATH.pub.pem"
chmod 644 "$KEY_PATH.pub.pem"

echo "Private key: $KEY_PATH"
echo "Public key:  $KEY_PATH.pub.pem"
echo "Set ID_APP_SIGNING_KEY=$KEY_PATH on the controlled release machine."
